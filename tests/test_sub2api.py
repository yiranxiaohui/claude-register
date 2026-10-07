"""sub2api 对接：列出 Claude OAuth 账号、匹配本地账号、重新授权并写回 sub2api。"""
from __future__ import annotations

import json
from unittest import mock

import httpx
import pytest
from fastapi.testclient import TestClient

from claude_register import oauth
from server import db, sub2api
from server.app import create_app
from server.config_store import REDACTED, load_config, save_config

ADMIN_KEY = "admin-secret-key"
SK = "sk-ant-sid01-secret"


def _tokens(email="a@x.com"):
    return oauth.OAuthTokens(
        access_token="sk-ant-oat01-NEW", refresh_token="sk-ant-ort01-NEW", token_type="Bearer",
        expires_in=28800, expires_at=1_790_000_000, scope=oauth.SCOPE_FULL,
        org_uuid="org-1", account_uuid="acct-1", email_address=email,
    )


def _acct(id_, name, *, status="active", email=None, platform="anthropic", type_="oauth"):
    extra = {"email_address": email} if email else {}
    return {"id": id_, "name": name, "platform": platform, "type": type_, "status": status,
            "error_message": "invalid_grant: token revoked" if status == "error" else "",
            "schedulable": status == "active", "extra": extra,
            "credentials": {"expires_at": "1700000000", "scope": "x"},
            "credentials_status": {"has_access_token": True, "has_refresh_token": True},
            "updated_at": "2026-10-01T00:00:00Z"}


class FakeSub2API:
    def __init__(self, accounts, *, apply_status=200):
        self.accounts = {a["id"]: a for a in accounts}
        self.apply_status = apply_status
        self.calls: list[httpx.Request] = []
        self.applied: dict[int, dict] = {}

    def handle(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        if req.headers.get("x-api-key") != ADMIN_KEY:
            return httpx.Response(401, json={"code": "UNAUTHORIZED", "message": "Authorization required"})
        path = req.url.path
        if path == "/api/v1/admin/accounts" and req.method == "GET":
            q = req.url.params
            assert q["platform"] == "anthropic" and q["type"] == "oauth"
            page, size = int(q["page"]), int(q["page_size"])
            items = list(self.accounts.values())
            chunk = items[(page - 1) * size: page * size]
            pages = max(1, -(-len(items) // size))
            return httpx.Response(200, json={"code": 0, "message": "success", "data": {
                "items": chunk, "total": len(items), "page": page, "page_size": size,
                "pages": pages}})
        parts = path.rsplit("/", 2)
        if req.method == "GET" and path.startswith("/api/v1/admin/accounts/"):
            acct = self.accounts.get(int(path.rsplit("/", 1)[1]))
            if acct is None:
                return httpx.Response(404, json={"code": "NOT_FOUND", "message": "Account not found"})
            return httpx.Response(200, json={"code": 0, "data": acct})
        if req.method == "POST" and parts[-1] == "apply-oauth-credentials":
            if self.apply_status != 200:
                return httpx.Response(self.apply_status, json={"code": "X", "message": "boom"})
            aid = int(parts[-2])
            body = json.loads(req.content)
            self.applied[aid] = body
            updated = {**self.accounts[aid], "status": "active", "error_message": "",
                       "credentials": {"expires_at": body["credentials"]["expires_at"]}}
            self.accounts[aid] = updated
            return httpx.Response(200, json={"code": 0, "data": updated})
        return httpx.Response(404, text="404 page not found")


@pytest.fixture
def env(tmp_path, monkeypatch):
    save_config(tmp_path / "config.yaml", {
        "panel_password": "pw", "sub2api_base_url": "http://s2a.test:8080/api/v1/",
        "sub2api_admin_key": ADMIN_KEY,
    })
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml",
                     now_fn=lambda: "2026-10-07T00:00:00Z")
    conn = app.state.cr.conn
    db.upsert_account(conn, "a@x.com", "x.com", "", "", 1, "success",
                      session_key=SK, proxy="http://p:1")
    db.upsert_account(conn, "b@x.com", "x.com", "", "", 2, "success")  # 无 sessionKey
    fake = FakeSub2API([
        _acct(1, "a@x.com", status="error"),
        _acct(2, "Team B", status="error", email="B@x.com"),
        _acct(3, "c@x.com"),
        _acct(4, "no-email-here", status="error"),
    ])
    monkeypatch.setattr(sub2api, "TRANSPORT", httpx.MockTransport(fake.handle))
    c = TestClient(app)
    c.post("/api/login", json={"password": "pw"})
    return app, c, fake


def test_config_redacts_admin_key(tmp_path):
    path = tmp_path / "config.yaml"
    save_config(path, {"panel_password": "pw", "sub2api_base_url": "http://s",
                       "sub2api_admin_key": ADMIN_KEY})
    app = create_app(data_dir=tmp_path, config_path=path)
    c = TestClient(app)
    c.post("/api/login", json={"password": "pw"})
    cfg = c.get("/api/config").json()
    assert cfg["sub2api_admin_key"] == REDACTED and cfg["sub2api_base_url"] == "http://s"
    assert ADMIN_KEY not in json.dumps(cfg)
    c.put("/api/config", json={**cfg, "panel_password": ""})  # 回传占位符不覆盖
    assert load_config(path).sub2api_admin_key == ADMIN_KEY


def test_list_accounts_matches_local(env):
    _, c, fake = env
    r = c.get("/api/sub2api/accounts")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["base_url"] == "http://s2a.test:8080"
    by_id = {a["id"]: a for a in body["accounts"]}
    assert by_id[1]["email"] == "a@x.com" and by_id[1]["local"]["has_session_key"] is True
    assert by_id[1]["status"] == "error" and "invalid_grant" in by_id[1]["error_message"]
    assert by_id[1]["token_expires_at"] == "2023-11-14T22:13:20Z"
    assert by_id[2]["email"] == "b@x.com" and by_id[2]["local"]["has_session_key"] is False
    assert by_id[3]["local"] is None and by_id[4]["email"] == ""
    assert SK not in r.text and ADMIN_KEY not in r.text
    assert str(fake.calls[0].url).startswith("http://s2a.test:8080/api/v1/admin/accounts?")


def test_list_paginates(env, monkeypatch):
    _, c, fake = env
    monkeypatch.setattr(sub2api, "PAGE_SIZE", 3)
    assert len(c.get("/api/sub2api/accounts").json()["accounts"]) == 4
    assert len(fake.calls) == 2


def test_reauth_pushes_tokens(env):
    app, c, fake = env
    with mock.patch.object(oauth, "authorize_with_session_key", return_value=_tokens()) as m:
        r = c.post("/api/sub2api/accounts/1/reauth")
    assert r.status_code == 200, r.text
    assert m.call_args.args == (SK, "http://p:1")
    body = fake.applied[1]
    assert body["type"] == "oauth"
    # oauth_at (2026-10-07) 晚于测试令牌的 expires_at，故不带 expires_in
    assert body["credentials"] == {
        "access_token": "sk-ant-oat01-NEW", "refresh_token": "sk-ant-ort01-NEW",
        "token_type": "Bearer", "scope": oauth.SCOPE_FULL, "expires_at": "1790000000",
    }
    assert body["extra"] == {"org_uuid": "org-1", "account_uuid": "acct-1",
                             "email_address": "a@x.com"}
    out = r.json()
    assert out["account"]["status"] == "active" and out["email"] == "a@x.com"
    assert "sk-ant-oat01" not in r.text and "sk-ant-ort01" not in r.text
    row = db.get_account(app.state.cr.conn, "a@x.com")
    assert row["access_token"] == "sk-ant-oat01-NEW"


def test_apply_payload_shape():
    row = {"email": "A@x.com", "access_token": "at", "refresh_token": "rt",
           "oauth_scope": "s", "oauth_expires_at": "2026-10-07T08:00:00Z",
           "oauth_at": "2026-10-07T00:00:00Z", "org_uuid": "o", "account_uuid": ""}
    p = sub2api.apply_payload(row)
    assert p == {"type": "oauth", "credentials": {
        "access_token": "at", "token_type": "Bearer", "scope": "s", "refresh_token": "rt",
        "expires_at": "1791360000", "expires_in": "28800"},
        "extra": {"org_uuid": "o", "email_address": "a@x.com"}}


def test_reauth_refuses_mismatched_token_email(env):
    app, c, fake = env
    with mock.patch.object(oauth, "authorize_with_session_key",
                           return_value=_tokens(email="other@x.com")):
        r = c.post("/api/sub2api/accounts/1/reauth")
    assert r.status_code == 422 and "不一致" in r.json()["detail"]
    assert fake.applied == {}
    assert not db.get_account(app.state.cr.conn, "a@x.com")["access_token"]


def test_reauth_errors(env):
    _, c, fake = env
    assert c.post("/api/sub2api/accounts/2/reauth").status_code == 400   # 本地无 sessionKey
    assert c.post("/api/sub2api/accounts/3/reauth").status_code == 404   # 本地无此账号
    assert c.post("/api/sub2api/accounts/4/reauth").status_code == 400   # 识别不出邮箱
    assert c.post("/api/sub2api/accounts/99/reauth").status_code == 502  # sub2api 404
    fake.accounts[5] = _acct(5, "a@x.com", type_="apikey")
    assert c.post("/api/sub2api/accounts/5/reauth").status_code == 400
    with mock.patch.object(oauth, "authorize_with_session_key",
                           side_effect=oauth.OAuthError("获取组织：sessionKey 已失效", kind="dead")):
        r = c.post("/api/sub2api/accounts/1/reauth")
    assert r.status_code == 422 and fake.applied == {}


def test_reauth_push_failure(env):
    _, c, fake = env
    fake.apply_status = 500
    with mock.patch.object(oauth, "authorize_with_session_key", return_value=_tokens()):
        r = c.post("/api/sub2api/accounts/1/reauth")
    assert r.status_code == 502 and "写回 sub2api 失败" in r.json()["detail"]


def test_bad_key_and_unconfigured(env, tmp_path):
    app, c, _ = env
    save_config(app.state.cr.config_path, {"sub2api_admin_key": "wrong"})
    r = c.get("/api/sub2api/accounts")
    assert r.status_code == 502 and "API Key 无效" in r.json()["detail"]
    assert "wrong" not in r.text
    save_config(app.state.cr.config_path, {"sub2api_base_url": ""})
    r = c.get("/api/sub2api/accounts")
    assert r.status_code == 400 and "未配置" in r.json()["detail"]


def test_network_error_message(env, monkeypatch):
    _, c, _ = env

    def boom(req):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(sub2api, "TRANSPORT", httpx.MockTransport(boom))
    r = c.get("/api/sub2api/accounts")
    assert r.status_code == 502 and "ConnectError" in r.json()["detail"]


def test_requires_panel_auth(env):
    app, _, _ = env
    anon = TestClient(app)
    assert anon.get("/api/sub2api/accounts").status_code == 401
    assert anon.post("/api/sub2api/accounts/1/reauth").status_code == 401
