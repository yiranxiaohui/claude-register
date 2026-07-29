"""server.app 集成测试：登录、脱敏配置、鉴权、忙时 409。"""
from __future__ import annotations

import time

from fastapi.testclient import TestClient

from server.app import create_app
from server.config_store import save_config


def _client(tmp_path):
    app = create_app(
        data_dir=tmp_path,
        config_path=tmp_path / "config.yaml",
        now_fn=lambda: "2026-07-29T00:00:00Z",
    )
    return TestClient(app)


def test_login_wrong_password(tmp_path):
    save_config(tmp_path / "config.yaml", {"panel_password": "pw"})
    c = _client(tmp_path)
    r = c.post("/api/login", json={"password": "nope"})
    assert r.status_code == 401


def test_login_and_get_config_redacted(tmp_path):
    save_config(tmp_path / "config.yaml", {"panel_password": "pw", "anymail_api_key": "ak_1"})
    c = _client(tmp_path)
    assert c.post("/api/login", json={"password": "pw"}).status_code == 200
    r = c.get("/api/config")
    assert r.status_code == 200
    assert r.json()["anymail_api_key"] == "••••"


def test_runs_requires_auth(tmp_path):
    save_config(tmp_path / "config.yaml", {"panel_password": "pw"})
    c = _client(tmp_path)
    assert c.get("/api/runs").status_code == 401


def test_start_run_busy_returns_409(tmp_path, monkeypatch):
    save_config(
        tmp_path / "config.yaml",
        {"panel_password": "pw", "anymail_api_key": "ak", "anymail_base_url": "https://m"},
    )
    c = _client(tmp_path)
    c.post("/api/login", json={"password": "pw"})

    import claude_register.flow as flow

    monkeypatch.setattr(flow, "run", lambda **kw: time.sleep(0.5))

    r1 = c.post("/api/runs", json={"email": "a@x.com"})
    assert r1.status_code == 200
    r2 = c.post("/api/runs", json={"email": "b@x.com"})
    assert r2.status_code == 409
