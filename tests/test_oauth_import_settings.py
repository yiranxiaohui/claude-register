import json

import httpx
import pytest
from fastapi.testclient import TestClient

from server import oauth_import, oauth_import_settings, sub2api
from server.app import create_app
from server.config_store import save_config

SOURCE = {"credentials": {"access_token": "private-access", "refresh_token": "private-refresh",
                          "model_mapping": {"original": "original"}}}
HEADERS = {"content-type": oauth_import.BATCH_CONTENT_TYPE}


def batch(settings):
    return {"files": [{"filename": "accounts.json", "content": json.dumps([SOURCE, SOURCE])}],
            "settings": settings}


@pytest.fixture
def env(tmp_path, monkeypatch):
    save_config(tmp_path / "config.yaml", {"panel_password": "test-password",
                "sub2api_base_url": "http://sub2api.test", "sub2api_admin_key": "private-admin"})
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml")
    calls = []
    state = {"created": 2, "failed": 0, "results": [], "post_status": 200}

    def handle(req):
        calls.append(req)
        assert req.headers["x-api-key"] == "private-admin"
        if req.url.path.endswith("/groups/all"):
            assert req.url.params["platform"] == "anthropic"
            return httpx.Response(200, json={"data": [
                {"id": 2, "name": "Claude", "platform": "anthropic", "status": "active", "private": "hidden"},
                {"id": 3, "name": "Other", "platform": "openai", "status": "active"},
                {"id": 4, "name": "Disabled", "platform": "anthropic", "status": "disabled"},
            ]})
        if req.url.path.endswith("/proxies/all"):
            return httpx.Response(200, json={"data": [
                {"id": 7, "name": "Japan", "status": "active", "username": "hidden-user", "password": "hidden-password", "host": "private-host"},
                {"id": 8, "name": "Disabled", "status": "inactive"},
            ]})
        assert req.method == "POST" and req.url.path == "/api/v1/admin/accounts/batch"
        assert req.headers["Idempotency-Key"]
        return httpx.Response(state["post_status"], json={"data": {
            "success": state["created"], "failed": state["failed"], "results": state["results"],
        }, "message": "private-access private-refresh private-admin"})

    monkeypatch.setattr(sub2api, "TRANSPORT", httpx.MockTransport(handle))
    with TestClient(app) as c:
        c.post("/api/login", json={"password": "test-password"})
        yield c, calls, state


def test_options_are_authenticated_active_and_sanitized(env):
    c, calls, _ = env
    r = c.get("/api/sub2api/import/options")
    assert r.status_code == 200
    assert r.json() == {"groups": [{"id": 2, "name": "Claude"}], "proxies": [{"id": 7, "name": "Japan"}]}
    c.cookies.clear()
    assert c.get("/api/sub2api/import/options").status_code == 401
    assert len(calls) == 2


def test_creation_parameters_reach_batch_api(env):
    c, calls, _ = env
    settings = {"names": ["First", "Second"], "notes": "New batch", "concurrency": 12, "priority": 0,
                "rate_multiplier": 0, "proxy_id": 7, "group_ids": [2, 2], "expires_at": 1893456000,
                "auto_pause_on_expired": False, "model_mode": "whitelist", "models": ["claude-sonnet"]}
    r = c.post("/api/sub2api/import/preview", json=batch(settings), headers=HEADERS)
    assert r.status_code == 200 and not calls
    assert [a["name"] for a in r.json()["accounts"]] == settings["names"]
    assert all(a["concurrency"] == 12 and a["priority"] == 0 for a in r.json()["accounts"])
    assert "private-" not in r.text
    r = c.post("/api/sub2api/import", json=batch(settings), headers=HEADERS)
    assert r.json() == {"created": 2, "failed": 0, "failed_names": []}
    assert len(calls) == 3
    accounts = json.loads(calls[-1].content)["accounts"]
    for index, account in enumerate(accounts):
        assert account == {
            "name": settings["names"][index], "notes": "New batch", "platform": "anthropic", "type": "oauth",
            "credentials": {**SOURCE["credentials"], "model_mapping": {"claude-sonnet": "claude-sonnet"}},
            "concurrency": 12, "priority": 0, "rate_multiplier": 0, "proxy_id": 7, "group_ids": [2],
            "expires_at": 1893456000, "auto_pause_on_expired": False,
        }


@pytest.mark.parametrize("settings, expected", [
    ({"name_prefix": "batch-"}, ["batch-accounts-1", "batch-accounts-2"]),
    ({"name_prefix": "  批次-  ", "names": [" First ", "Second"]}, ["批次-First", "批次-Second"]),
    ({"name_prefix": ""}, ["accounts-1", "accounts-2"]),
    ({"name_prefix": "   "}, ["accounts-1", "accounts-2"]),
    ({"name_prefix": "前" * 99, "names": ["甲", "乙"]}, ["前" * 99 + "甲", "前" * 99 + "乙"]),
])
def test_name_prefix_matches_preview_and_created_names(env, settings, expected):
    c, calls, _ = env
    source = batch(settings)
    r = c.post("/api/sub2api/import/preview", json=source, headers=HEADERS)
    assert r.status_code == 200 and not calls
    assert [a["name"] for a in r.json()["accounts"]] == expected
    assert "private-" not in r.text
    r = c.post("/api/sub2api/import", json=source, headers=HEADERS)
    assert r.status_code == 200
    accounts = json.loads(calls[-1].content)["accounts"]
    assert [a["name"] for a in accounts] == expected
    assert all(a["credentials"] == SOURCE["credentials"] for a in accounts)
    assert all("name_prefix" not in a for a in accounts)


def test_prefix_applies_across_files_and_preserves_source_names(env):
    c, calls, _ = env
    source = {"files": [
        {"filename": "first.json", "content": json.dumps({**SOURCE, "name": "Original"})},
        {"filename": "second.json", "content": json.dumps(SOURCE)},
    ], "settings": {"name_prefix": "batch-"}}
    r = c.post("/api/sub2api/import/preview", json=source, headers=HEADERS)
    assert r.status_code == 200 and not calls
    assert [(a["name"], a["source_file"]) for a in r.json()["accounts"]] == [
        ("batch-Original", "first.json"), ("batch-second", "second.json"),
    ]
    r = c.post("/api/sub2api/import", json=source, headers=HEADERS)
    assert r.status_code == 200
    assert [a["name"] for a in json.loads(calls[-1].content)["accounts"]] == ["batch-Original", "batch-second"]


def test_name_prefix_does_not_mutate_payload_or_accumulate():
    original = oauth_import.convert({**SOURCE, "name": "Original"})
    settings = {"name_prefix": "batch-"}
    for _ in range(2):
        result = oauth_import_settings.apply_settings(original, settings)
        assert result["accounts"][0]["name"] == "batch-Original"
    assert original["accounts"][0]["name"] == "Original"
    assert original["accounts"][0]["credentials"] == SOURCE["credentials"]
    assert settings == {"name_prefix": "batch-"}


@pytest.mark.parametrize("path", ["/api/sub2api/import/preview", "/api/sub2api/import"])
@pytest.mark.parametrize("settings", [
    {"name_prefix": None}, {"name_prefix": 1}, {"name_prefix": True}, {"name_prefix": []},
    {"name_prefix": {}}, {"name_prefix": "x" * 101}, {"name_prefix": "\ud800"},
    {"name_prefix": "x" * 100}, {"name_prefix": "batch-", "names": ["Good", "x" * 95]},
    {"name_prefix": "batch-", "names": ["Good", " "]},
])
def test_invalid_prefix_blocks_entire_batch(env, path, settings):
    c, calls, _ = env
    r = c.post(path, content=json.dumps(batch(settings)), headers=HEADERS)
    assert r.status_code == 400 and not calls
    assert "private-" not in r.text


def test_file_values_and_model_modes_are_preserved():
    original = oauth_import.convert({**SOURCE, "concurrency": 8, "priority": 9})
    result = oauth_import_settings.apply_settings(original, {})["accounts"][0]
    assert result["concurrency"] == 8 and result["priority"] == 9
    assert result["credentials"]["model_mapping"] == {"original": "original"}
    assert result["group_ids"] == [] and result["proxy_id"] is None
    assert result["auto_pause_on_expired"] is True and result["rate_multiplier"] == 1
    assert "model_mapping" not in oauth_import_settings.apply_settings(original, {"model_mode": "all"})["accounts"][0]["credentials"]
    result = oauth_import_settings.apply_settings(original, {"model_mode": "mapping", "model_mapping": {"claude-*": "claude-sonnet"}})
    assert result["accounts"][0]["credentials"]["model_mapping"] == {"claude-*": "claude-sonnet"}
    assert original["accounts"][0]["credentials"] == SOURCE["credentials"]


@pytest.mark.parametrize("settings", [
    None, [], {"credentials": {"access_token": "bad"}}, {"concurrency": 0}, {"concurrency": True},
    {"priority": -1}, {"priority": 1.5}, {"rate_multiplier": True}, {"rate_multiplier": "1"},
    {"rate_multiplier": -1}, {"rate_multiplier": 10**100}, {"rate_multiplier": float("inf")},
    {"notes": []}, {"proxy_id": "7"}, {"group_ids": [True]}, {"group_ids": "2"},
    {"names": ["only-one"]}, {"names": ["Good", " "]}, {"names": ["Good", "x" * 101]},
    {"names": ["Good", "\ud800"]}, {"expires_at": -1}, {"auto_pause_on_expired": "false"},
    {"model_mode": "invalid"}, {"model_mode": "whitelist", "models": []},
    {"model_mode": "whitelist", "models": ["claude-*"]},
    {"model_mode": "mapping", "model_mapping": {"cl*de": "claude"}},
    {"model_mode": "mapping", "model_mapping": {"claude": "claude-*"}},
])
def test_invalid_parameters_block_all_creation(env, settings):
    c, calls, _ = env
    r = c.post("/api/sub2api/import", content=json.dumps(batch(settings)), headers=HEADERS)
    assert r.status_code == 400
    assert not calls
    assert "private-access" not in r.text


@pytest.mark.parametrize("settings", [{"proxy_id": 8}, {"group_ids": [3]}, {"group_ids": [4]}])
def test_stale_or_wrong_platform_options_block_creation(env, settings):
    c, calls, _ = env
    r = c.post("/api/sub2api/import", json=batch(settings), headers=HEADERS)
    assert r.status_code == 400
    assert all(call.method == "GET" for call in calls)


def test_partial_failure_returns_prefixed_name(env):
    c, _, state = env
    state.update(created=1, failed=1, results=[
        {"success": False, "name": "batch-accounts-2", "error": "private-access"},
        {"success": False, "name": "private-admin"},
    ])
    r = c.post("/api/sub2api/import", json=batch({"name_prefix": "batch-"}), headers=HEADERS)
    assert r.status_code == 200
    assert r.json() == {"created": 1, "failed": 1, "failed_names": ["batch-accounts-2"]}
    assert "private-" not in r.text


def test_partial_failure_and_upstream_errors_are_sanitized(env):
    c, calls, state = env
    state.update(created=1, failed=1, results=[{"success": False, "name": "accounts-2", "error": "private-access"},
                                            {"success": False, "name": "private-admin"}])
    r = c.post("/api/sub2api/import", json=batch({}), headers=HEADERS)
    assert r.json() == {"created": 1, "failed": 1, "failed_names": ["accounts-2"]}
    state["post_status"] = 403
    r = c.post("/api/sub2api/import", json=batch({}), headers=HEADERS)
    assert r.status_code == 502 and "403" in r.json()["detail"]
    assert "private-" not in r.text
