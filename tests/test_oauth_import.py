"""上传转换与真实 Sub2API 请求协议：不调用外部服务或保存账号凭据。"""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from server import db, oauth_import, sub2api
from server.app import create_app
from server.config_store import save_config

ACCESS = "fixture-private-access"
REFRESH = "fixture-private-refresh"
KEY = "fixture-admin-key"
SOURCE = {"claudeAiOauth": {
    "accessToken": ACCESS, "refreshToken": REFRESH,
    "expiresAt": 1794000000123, "scopes": ["user:profile", "user:inference"],
    "subscriptionType": "team", "rateLimitTier": "default",
}}


def test_claude_code_bom_and_metadata_conversion():
    raw = ("\ufeff" + json.dumps(SOURCE)).encode()
    payload = oauth_import.parse_file(raw, "账户.json")
    assert payload["type"] == "sub2api-data" and payload["version"] == 1
    assert payload["proxies"] == [] and payload["exported_at"].endswith("Z")
    assert payload["accounts"] == [{
        "name": "账户", "platform": "anthropic", "type": "oauth",
        "concurrency": 1, "priority": 50,
        "credentials": {"access_token": ACCESS, "refresh_token": REFRESH,
                        "expires_at": "1794000000", "scope": "user:profile user:inference",
                        "subscription_type": "team", "rate_limit_tier": "default",
                        "token_type": "Bearer"},
    }]


@pytest.mark.parametrize("wrapper", [lambda x: x, lambda x: {"accounts": x}])
def test_account_arrays_keep_credentials_and_choose_names(wrapper):
    source = [
        {"name": " Named ", "email": "email@example.com", "credentials": {"access_token": ACCESS},
         "concurrency": 0, "priority": 9},
        {"email": "email@example.com", "credentials": {"access_token": ACCESS}},
        {"credentials": {"access_token": ACCESS, "email_address": "credential@example.com",
                         "model_mapping": {"claude-sonnet": "claude-sonnet"}}},
        SOURCE,
    ]
    accounts = oauth_import.convert(wrapper(source), "batch")["accounts"]
    assert [a["name"] for a in accounts] == ["Named", "email@example.com", "credential@example.com", "batch-4"]
    assert accounts[0]["concurrency"] == 0 and accounts[0]["priority"] == 9
    assert accounts[2]["credentials"] == source[2]["credentials"]
    assert source[0]["name"] == " Named "


@pytest.mark.parametrize("source, message", [
    (None, "输入必须"), ([], "不能为空"), ({"accounts": {}}, "不能为空"),
    ([SOURCE, None], "第 2 个账号必须是对象"),
    ({"claudeAiOauth": []}, "claudeAiOauth 必须是对象"),
    ({"credentials": {"access_token": " "}}, "access_token"),
    ({"credentials": {"access_token": True}}, "access_token"),
    ({**SOURCE, "platform": "openai"}, "不是 Claude OAuth"),
    ({**SOURCE, "type": "setup-token"}, "不是 Claude OAuth"),
    ({**SOURCE, "name": " "}, "名称无效"),
    ({**SOURCE, "concurrency": True}, "concurrency"),
    ({**SOURCE, "priority": -1}, "priority"),
    ({**SOURCE, "priority": 2**63}, "priority"),
    ({"claudeAiOauth": {"accessToken": ACCESS, "expiresAt": "private-not-a-timestamp"}}, "时间戳"),
    ({"claudeAiOauth": {"accessToken": ACCESS, "scopes": [1]}}, "scopes"),
])
def test_invalid_accounts(source, message):
    with pytest.raises(ValueError, match=message) as exc:
        oauth_import.convert(source)
    assert ACCESS not in str(exc.value) and "private-not-a-timestamp" not in str(exc.value)


@pytest.mark.parametrize("raw, message", [
    (b"\xff", "UTF-8"), (b'{"credentials":', "JSON 格式错误"),
    (b'{"credentials":{"access_token":"secret","x":NaN}}', "NaN"),
    (b'{"credentials":{"access_token":"secret","x":1e999}}', "无效数值"),
    pytest.param(b"[" * 2000, "JSON 格式错误|嵌套层级", id="deep-malformed-json"),
    (b'{"name":"\\ud800","credentials":{"access_token":"secret"}}', "Unicode"),
])
def test_invalid_file(raw, message):
    with pytest.raises(ValueError, match=message):
        oauth_import.parse_file(raw)


def test_account_limit_and_string_scopes():
    with pytest.raises(ValueError, match="最多"):
        oauth_import.convert([SOURCE] * 1001)
    payload = oauth_import.convert({"claudeAiOauth": {"accessToken": ACCESS, "scopes": "user:profile"}})
    assert payload["accounts"][0]["credentials"]["scope"] == "user:profile"


@pytest.fixture
def env(tmp_path, monkeypatch):
    save_config(tmp_path / "config.yaml", {
        "panel_password": "test-password", "sub2api_base_url": "http://sub2api.test/api/v1/",
        "sub2api_admin_key": KEY,
    })
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml")
    calls = []

    def handle(request):
        calls.append(request)
        assert request.method == "POST" and request.url.path == "/api/v1/admin/accounts/data"
        assert request.headers["x-api-key"] == KEY
        assert len(request.headers["Idempotency-Key"]) == 36
        count = len(json.loads(request.content)["data"]["accounts"])
        return httpx.Response(200, json={"data": {"account_created": count, "account_failed": 0}})

    monkeypatch.setattr(sub2api, "TRANSPORT", httpx.MockTransport(handle))
    with TestClient(app) as c:
        c.post("/api/login", json={"password": "test-password"})
        yield c, calls, app


def test_preview_is_private_and_does_not_import(env):
    c, calls, app = env
    r = c.post("/api/sub2api/import/preview?filename=upload.json", json=SOURCE)
    assert r.status_code == 200
    assert r.json() == {"accounts": [{"name": "upload", "concurrency": 1, "priority": 50,
                                       "has_refresh_token": True}]}
    assert not calls and not db.list_accounts(app.state.cr.conn)
    for secret in (KEY, ACCESS, REFRESH):
        assert secret not in r.text


@pytest.mark.parametrize("bind", [False, True])
def test_import_sends_converted_data_with_group_choice(env, bind):
    c, calls, app = env
    r = c.post(f"/api/sub2api/import?filename=upload.json&bind_default_group={str(bind).lower()}",
               content=("\ufeff" + json.dumps(SOURCE)).encode())
    assert r.status_code == 200
    assert r.json() == {"created": 1, "failed": 0, "failed_names": []}
    body = json.loads(calls[0].content)
    assert body["skip_default_group_bind"] is (not bind)
    assert body["data"]["accounts"][0]["credentials"]["expires_at"] == "1794000000"
    assert not db.list_accounts(app.state.cr.conn)


@pytest.mark.parametrize("path", ["/api/sub2api/import", "/api/sub2api/import/preview"])
def test_auth_and_full_validation_before_import(env, monkeypatch, path):
    c, calls, _ = env
    assert c.post(path, json=[SOURCE, {}]).status_code == 400
    monkeypatch.setattr(oauth_import, "MAX_FILE_BYTES", 10)
    assert c.post(path, content=b"x" * 11).status_code == 413
    c.cookies.clear()
    assert c.post(path, json=SOURCE).status_code == 401
    assert calls == []


def test_partial_result_does_not_echo_upstream_credentials(env, monkeypatch):
    c, _, _ = env
    monkeypatch.setattr(sub2api, "TRANSPORT", httpx.MockTransport(lambda r: httpx.Response(200, json={
        "data": {"account_created": 1, "account_failed": 1, "credentials": ACCESS,
                 "errors": [{"kind": "account", "name": "batch-2", "message": REFRESH},
                            {"kind": "account", "name": KEY, "message": ACCESS}]},
    })))
    r = c.post("/api/sub2api/import?filename=batch.json", json=[SOURCE, SOURCE])
    assert r.json() == {"created": 1, "failed": 1, "failed_names": ["batch-2"]}
    assert all(secret not in r.text for secret in (KEY, ACCESS, REFRESH))


@pytest.mark.parametrize("status", [400, 401, 403, 500])
def test_upstream_errors_are_safe(env, monkeypatch, status):
    c, _, _ = env
    monkeypatch.setattr(sub2api, "TRANSPORT", httpx.MockTransport(lambda r: httpx.Response(
        status, json={"message": f"{KEY} {ACCESS} {REFRESH}"})))
    r = c.post("/api/sub2api/import", json=SOURCE)
    assert r.status_code == 502 and str(status) in r.json()["detail"]
    assert all(secret not in r.text for secret in (KEY, ACCESS, REFRESH))


def test_invalid_result_and_missing_config(env, monkeypatch):
    c, calls, app = env
    assert c.post("/api/sub2api/import?bind_default_group=nope", json=SOURCE).status_code == 400
    assert not calls
    monkeypatch.setattr(sub2api, "TRANSPORT", httpx.MockTransport(lambda r: httpx.Response(
        200, json={"data": {"account_created": 0, "account_failed": 0}})))
    r = c.post("/api/sub2api/import", json=SOURCE)
    assert r.status_code == 502 and "核对结果" in r.json()["detail"]
    save_config(app.state.cr.config_path, {"sub2api_base_url": ""})
    assert c.post("/api/sub2api/import", json=SOURCE).status_code == 400
