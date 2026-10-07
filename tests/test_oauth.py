"""Claude OAuth：sessionKey → PKCE 授权 → 令牌；落库、面板/开放 API、sub2api 导出、注册后自动授权。"""
from __future__ import annotations

import base64
import hashlib
import json
import time
from unittest import mock

import httpx
import pytest
from fastapi.testclient import TestClient

from claude_register import oauth
from server import db, export, oauth_acquire, runner
from server.app import create_app
from server.config_store import Config, load_config, save_config

SK = "sk-ant-sid01-secret"
ORG = "org-personal"
TEAM = "org-team"
KEY = "cr_test_key_0123456789"
H = {"Authorization": f"Bearer {KEY}"}
TOKEN_BODY = {
    "access_token": "sk-ant-oat01-ACCESS",
    "refresh_token": "sk-ant-ort01-REFRESH",
    "token_type": "Bearer",
    "expires_in": 28800,
    "scope": oauth.SCOPE_FULL,
    "organization": {"uuid": ORG},
    "account": {"uuid": "acct-1", "email_address": "A@X.com"},
}


def _b64url_sha256(text: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(text.encode()).digest()).rstrip(b"=").decode()


class FakeClaude:
    """模拟 claude.ai 组织/授权接口与 platform.claude.com 令牌接口，并校验 PKCE。"""

    def __init__(self, *, orgs=None, org_status=200, org_html=False, authorize_status=200,
                 token_status=200, token_body=None):
        self.orgs = orgs if orgs is not None else [{"uuid": ORG, "raven_type": None}]
        self.org_status = org_status
        self.org_html = org_html
        self.authorize_status = authorize_status
        self.token_status = token_status
        self.token_body = token_body if token_body is not None else TOKEN_BODY
        self.requests: list[httpx.Request] = []
        self.challenge = ""
        self.state = ""
        self.proxy = "unset"

    def factory(self, proxy=None):
        self.proxy = proxy
        return httpx.Client(transport=httpx.MockTransport(self.handle))

    def handle(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        url = str(req.url)
        if url == oauth.ORG_URL:
            assert f"sessionKey={SK}" in req.headers.get("cookie", "")
            if self.org_html:
                return httpx.Response(403, headers={"cf-mitigated": "challenge",
                                                    "content-type": "text/html"},
                                      text="<html>Just a moment</html>")
            if self.org_status != 200:
                return httpx.Response(self.org_status, json={"error": {"type": "auth"}})
            return httpx.Response(200, json=self.orgs)
        if url.endswith("/authorize"):
            assert f"sessionKey={SK}" in req.headers.get("cookie", "")
            body = json.loads(req.content)
            if self.authorize_status != 200:
                return httpx.Response(self.authorize_status, json={"error": "nope"})
            assert body["client_id"] == oauth.CLIENT_ID
            assert body["redirect_uri"] == oauth.REDIRECT_URI
            assert body["scope"] == oauth.SCOPE_FULL
            assert body["code_challenge_method"] == "S256"
            assert body["organization_uuid"] in url
            self.challenge = body["code_challenge"]
            self.state = body["state"]
            return httpx.Response(200, json={
                "redirect_uri": f"{oauth.REDIRECT_URI}?code=AUTHCODE&state={self.state}",
            })
        if url == oauth.TOKEN_URL:
            assert "cookie" not in req.headers  # 令牌接口不带 sessionKey
            assert req.headers["user-agent"] == oauth.TOKEN_USER_AGENT
            body = json.loads(req.content)
            assert body["grant_type"] == "authorization_code"
            assert body["code"] == "AUTHCODE"
            assert body["state"] == self.state
            assert _b64url_sha256(body["code_verifier"]) == self.challenge
            return httpx.Response(self.token_status, json=self.token_body)
        return httpx.Response(404)


# ---- claude_register.oauth ----


def test_generate_pkce_s256():
    verifier, challenge, state = oauth.generate_pkce()
    assert len(verifier) == 43 and len(state) == 43
    assert challenge == _b64url_sha256(verifier)
    assert "=" not in verifier + challenge + state


def test_authorize_success_full_flow():
    fake = FakeClaude()
    tokens = oauth.authorize_with_session_key(
        SK, "socks5://u:p@h:1", client_factory=fake.factory, now_fn=lambda: 1_000,
    )
    assert tokens.access_token == "sk-ant-oat01-ACCESS"
    assert tokens.refresh_token == "sk-ant-ort01-REFRESH"
    assert tokens.expires_in == 28800 and tokens.expires_at == 1_000 + 28800
    assert tokens.org_uuid == ORG and tokens.account_uuid == "acct-1"
    assert tokens.email_address == "a@x.com"
    assert tokens.scope == oauth.SCOPE_FULL
    assert [str(r.url).rsplit("/", 1)[-1] for r in fake.requests] == [
        "organizations", "authorize", "token"]
    assert fake.proxy == "socks5://u:p@h:1"


def test_authorize_prefers_team_org():
    fake = FakeClaude(orgs=[{"uuid": ORG}, {"uuid": TEAM, "raven_type": "team"}])
    oauth.authorize_with_session_key(SK, client_factory=fake.factory)
    assert f"/v1/oauth/{TEAM}/authorize" in str(fake.requests[1].url)


def test_authorize_dead_session_key():
    fake = FakeClaude(org_status=401)
    with pytest.raises(oauth.OAuthError) as ei:
        oauth.authorize_with_session_key(SK, client_factory=fake.factory)
    assert ei.value.kind == "dead"
    assert SK not in str(ei.value)


def test_authorize_cloudflare_blocked():
    fake = FakeClaude(org_html=True)
    with pytest.raises(oauth.OAuthError) as ei:
        oauth.authorize_with_session_key(SK, client_factory=fake.factory)
    assert ei.value.kind == "blocked"


def test_authorize_code_failure_and_token_failure():
    with pytest.raises(oauth.OAuthError, match="获取授权码"):
        oauth.authorize_with_session_key(SK, client_factory=FakeClaude(
            authorize_status=403).factory)
    with pytest.raises(oauth.OAuthError, match="换取令牌") as ei:
        oauth.authorize_with_session_key(SK, client_factory=FakeClaude(
            token_status=400, token_body={"error": "invalid_grant"}).factory)
    assert ei.value.kind == "error"


def test_authorize_no_orgs_and_no_access_token():
    with pytest.raises(oauth.OAuthError, match="没有组织"):
        oauth.authorize_with_session_key(SK, client_factory=FakeClaude(orgs=[]).factory)
    with pytest.raises(oauth.OAuthError, match="access_token"):
        oauth.authorize_with_session_key(SK, client_factory=FakeClaude(
            token_body={"token_type": "Bearer"}).factory)


def test_authorize_requires_session_key_and_wraps_network_errors():
    with pytest.raises(oauth.OAuthError, match="sessionKey"):
        oauth.authorize_with_session_key("")

    def boom(req):
        raise httpx.ConnectError("proxy down secret")

    with pytest.raises(oauth.OAuthError) as ei:
        oauth.authorize_with_session_key(
            SK, client_factory=lambda p: httpx.Client(transport=httpx.MockTransport(boom)))
    assert "ConnectError" in str(ei.value) and "secret" not in str(ei.value)


# ---- 落库 ----


def _tokens(**kw):
    base = dict(access_token="sk-ant-oat01-ACCESS", refresh_token="sk-ant-ort01-REFRESH",
                token_type="Bearer", expires_in=28800, expires_at=1_790_000_000,
                scope=oauth.SCOPE_FULL, org_uuid=ORG, account_uuid="acct-1",
                email_address="a@x.com")
    base.update(kw)
    return oauth.OAuthTokens(**base)


def test_migrates_old_db_with_oauth_columns(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE accounts (email TEXT UNIQUE, domain TEXT, created_at TEXT, "
                 "expires_at TEXT, mailbox_id TEXT, last_run_id INTEGER, status TEXT)")
    conn.commit()
    conn.close()
    conn = db.init_db(path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(accounts)")}
    assert set(db.OAUTH_FIELDS) <= cols


def test_acquire_stores_tokens(tmp_path):
    conn = db.init_db(tmp_path / "t.db")
    db.upsert_account(conn, "a@x.com", "x.com", "", "", 1, "success",
                      session_key=SK, proxy="http://p:1")
    seen = {}

    def authorize(sk, proxy):
        seen["args"] = (sk, proxy)
        return _tokens()

    row = oauth_acquire.acquire(conn, "a@x.com", now="2026-09-24T00:00:00Z", authorize=authorize)
    assert seen["args"] == (SK, "http://p:1")
    assert row["access_token"] == "sk-ant-oat01-ACCESS"
    assert row["refresh_token"] == "sk-ant-ort01-REFRESH"
    assert row["oauth_expires_at"] == oauth_acquire.iso_from_unix(1_790_000_000)
    assert row["org_uuid"] == ORG and row["account_uuid"] == "acct-1"
    assert row["oauth_at"] == "2026-09-24T00:00:00Z"
    assert oauth_acquire.unix_from_iso(row["oauth_expires_at"]) == 1_790_000_000


def test_acquire_dead_marks_check_status(tmp_path):
    conn = db.init_db(tmp_path / "t.db")
    db.upsert_account(conn, "a@x.com", "x.com", "", "", 1, "success", session_key=SK)

    def authorize(sk, proxy):
        raise oauth.OAuthError("dead", kind="dead")

    with pytest.raises(oauth.OAuthError):
        oauth_acquire.acquire(conn, "a@x.com", now="T", authorize=authorize)
    row = db.get_account(conn, "a@x.com")
    assert row["check_status"] == "dead" and not row["access_token"]
    with pytest.raises(oauth_acquire.AccountNotFound):
        oauth_acquire.acquire(conn, "nobody@x.com", now="T", authorize=authorize)


# ---- 面板接口 ----


def _app(tmp_path, **cfg):
    base = {"panel_password": "pw", "api_enabled": True, "api_key": KEY}
    base.update(cfg)
    save_config(tmp_path / "config.yaml", base)
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml",
                     now_fn=lambda: "2026-09-24T00:00:00Z")
    conn = app.state.cr.conn
    db.upsert_account(conn, "a@x.com", "x.com", "", "", 1, "success",
                      session_key=SK, proxy="socks5://u:p@h:1080", display_name="备注")
    db.upsert_account(conn, "nosk@x.com", "x.com", "", "", 2, "needs_manual")
    return app


def _panel(tmp_path, **cfg):
    app = _app(tmp_path, **cfg)
    c = TestClient(app)
    c.post("/api/login", json={"password": "pw"})
    return app, c


def test_panel_oauth_success(tmp_path):
    app, c = _panel(tmp_path)
    with mock.patch.object(oauth, "authorize_with_session_key", return_value=_tokens()) as m:
        r = c.post("/api/accounts/a@x.com/oauth")
    assert r.status_code == 200, r.text
    assert m.call_args.args == (SK, "socks5://u:p@h:1080")
    body = r.json()
    assert body["access_token"] == "sk-ant-oat01-ACCESS"
    assert body["oauth_at"] == "2026-09-24T00:00:00Z"
    listed = {a["email"]: a for a in c.get("/api/accounts").json()}
    assert listed["a@x.com"]["refresh_token"] == "sk-ant-ort01-REFRESH"


def test_panel_oauth_errors(tmp_path):
    app, c = _panel(tmp_path)
    assert c.post("/api/accounts/nobody@x.com/oauth").status_code == 404
    assert c.post("/api/accounts/nosk@x.com/oauth").status_code == 400
    err = oauth.OAuthError("获取组织：sessionKey 已失效（HTTP 401）", kind="dead")
    with mock.patch.object(oauth, "authorize_with_session_key", side_effect=err):
        r = c.post("/api/accounts/a@x.com/oauth")
    assert r.status_code == 422 and "已失效" in r.json()["detail"]
    assert db.get_account(app.state.cr.conn, "a@x.com")["check_status"] == "dead"


def test_panel_oauth_requires_auth(tmp_path):
    app = _app(tmp_path)
    assert TestClient(app).post("/api/accounts/a@x.com/oauth").status_code == 401


# ---- 开放 API ----


def test_open_api_oauth(tmp_path):
    app = _app(tmp_path)
    c = TestClient(app)
    with mock.patch.object(oauth, "authorize_with_session_key", return_value=_tokens()):
        r = c.post("/api/v1/accounts/oauth", json={"email": "A@x.com"}, headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["email"] == "a@x.com"
    assert set(body["account"]) == {"email", "access_token", "refresh_token",
                                    "oauth_expires_at", "org_uuid", "account_uuid"}
    assert body["account"]["access_token"] == "sk-ant-oat01-ACCESS"
    assert "export" not in body

    with mock.patch.object(oauth, "authorize_with_session_key", return_value=_tokens()):
        r = c.post("/api/v1/accounts/oauth?format=line&fields=email,access_token,refresh_token",
                   json={"email": "a@x.com"}, headers=H)
    assert r.json()["export"] == "a@x.com----sk-ant-oat01-ACCESS----sk-ant-ort01-REFRESH\n"


def test_open_api_oauth_errors(tmp_path):
    app = _app(tmp_path)
    c = TestClient(app)
    assert c.post("/api/v1/accounts/oauth", json={"email": "a@x.com"}).status_code == 401
    assert c.post("/api/v1/accounts/oauth", json={}, headers=H).status_code == 400
    assert c.post("/api/v1/accounts/oauth", json={"email": "no@x.com"}, headers=H).status_code == 404
    assert c.post("/api/v1/accounts/oauth", json={"email": "nosk@x.com"},
                  headers=H).status_code == 400
    assert c.post("/api/v1/accounts/oauth?fields=bogus", json={"email": "a@x.com"},
                  headers=H).status_code == 400
    with mock.patch.object(oauth, "authorize_with_session_key",
                           side_effect=oauth.OAuthError("换取令牌：HTTP 400")):
        r = c.post("/api/v1/accounts/oauth", json={"email": "a@x.com"}, headers=H)
    assert r.status_code == 422


# ---- 导出 ----


def test_export_oauth_fields_and_sub2api(tmp_path):
    app, c = _panel(tmp_path)
    with mock.patch.object(oauth, "authorize_with_session_key", return_value=_tokens()):
        c.post("/api/accounts/a@x.com/oauth")
    r = c.get("/api/accounts/export?format=line&fields=email,access_token,oauth_expires_at")
    assert sorted(r.text.splitlines()) == [
        f"a@x.com----sk-ant-oat01-ACCESS----{oauth_acquire.iso_from_unix(1_790_000_000)}",
        "nosk@x.com--------",
    ]

    r = c.get("/api/accounts/export?format=sub2api")
    assert r.status_code == 200
    assert r.headers["x-total-count"] == "1"
    data = json.loads(r.text)
    assert data["type"] == "sub2api-data" and data["version"] == 1
    assert data["proxies"] == [{
        "proxy_key": "socks5|h|1080|u|p", "name": "h:1080", "protocol": "socks5",
        "host": "h", "port": 1080, "username": "u", "password": "p", "status": "active",
    }]
    (acct,) = data["accounts"]
    assert acct["name"] == "a@x.com"
    assert acct["platform"] == "anthropic" and acct["type"] == "oauth"
    assert acct["proxy_key"] == "socks5|h|1080|u|p"
    assert acct["notes"] == "备注"
    assert acct["credentials"]["access_token"] == "sk-ant-oat01-ACCESS"
    assert acct["credentials"]["refresh_token"] == "sk-ant-ort01-REFRESH"
    assert acct["credentials"]["expires_at"] == "1790000000"
    assert acct["credentials"]["scope"] == oauth.SCOPE_FULL
    assert acct["extra"] == {"org_uuid": ORG, "account_uuid": "acct-1",
                             "email_address": "a@x.com"}
    assert acct["concurrency"] > 0


def test_sub2api_payload_skips_unsupported_proxy_and_placeholder_email():
    rows = [{"email": "sk-abc@sk-import.local", "access_token": "t", "proxy": "socks4://h:1",
             "oauth_expires_at": "", "oauth_at": ""},
            {"email": "b@x.com", "access_token": "", "proxy": "http://h:2"}]
    data = export.sub2api_payload(rows, exported_at="2026-09-24T00:00:00Z")
    assert data["proxies"] == []
    (acct,) = data["accounts"]
    assert "proxy_key" not in acct and "extra" not in acct
    assert "expires_at" not in acct["credentials"]


def test_describe_lists_oauth_fields_and_format():
    meta = export.describe()
    keys = {f["key"]: f for f in meta["fields"]}
    assert keys["access_token"]["secret"] and keys["refresh_token"]["secret"]
    assert "sub2api" in meta["formats"]


# ---- 配置 / 注册后自动授权 ----


def test_config_oauth_auto_roundtrip(tmp_path):
    path = tmp_path / "config.yaml"
    assert load_config(path).oauth_auto_after_register is False
    save_config(path, {"oauth_auto_after_register": True})
    assert load_config(path).oauth_auto_after_register is True
    assert "auto_after_register: true" in path.read_text(encoding="utf-8")


def _wait(conn, rid):
    for _ in range(100):
        if db.get_run(conn, rid)["status"] != "running":
            return
        time.sleep(0.05)


def _flow(**kw):
    return {"email": "a@x.com", "sessionKey": SK, "proxy": "http://p:1"}


@pytest.mark.parametrize("enabled", [True, False])
def test_runner_auto_oauth(tmp_path, enabled):
    conn = db.init_db(tmp_path / "t.db")
    r = runner.Runner(conn, tmp_path, lambda: "2026-09-24T00:00:00Z")
    with mock.patch.object(oauth, "authorize_with_session_key", return_value=_tokens()) as m:
        rid = r.start(Config(oauth_auto_after_register=enabled), flow_fn=_flow)
        _wait(conn, rid)
    row = db.get_account(conn, "a@x.com")
    assert db.get_run(conn, rid)["status"] == "success"
    assert bool(row["access_token"]) is enabled
    assert m.called is enabled
    log_txt = (tmp_path / "runs" / str(rid) / "log.txt").read_text(encoding="utf-8")
    assert ("已获取 OAuth 令牌" in log_txt) is enabled
    assert "sk-ant-oat01" not in log_txt and "sk-ant-ort01" not in log_txt


def test_runner_auto_oauth_failure_keeps_success(tmp_path):
    conn = db.init_db(tmp_path / "t.db")
    r = runner.Runner(conn, tmp_path, lambda: "2026-09-24T00:00:00Z")
    with mock.patch.object(oauth, "authorize_with_session_key",
                           side_effect=oauth.OAuthError("获取授权码：HTTP 403")):
        rid = r.start(Config(oauth_auto_after_register=True), flow_fn=_flow)
        _wait(conn, rid)
    assert db.get_run(conn, rid)["status"] == "success"
    log_txt = (tmp_path / "runs" / str(rid) / "log.txt").read_text(encoding="utf-8")
    assert "OAuth 授权失败" in log_txt
