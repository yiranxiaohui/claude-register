"""session key 存活检测：POST /api/accounts/{email}/check。"""
from __future__ import annotations

from unittest import mock

from fastapi.testclient import TestClient

from server import db
from server.app import create_app
from server.config_store import save_config


def _app(tmp_path):
    save_config(tmp_path / "config.yaml", {"panel_password": "pw"})
    return create_app(
        data_dir=tmp_path,
        config_path=tmp_path / "config.yaml",
        now_fn=lambda: "2026-07-31T00:00:00Z",
    )


def _seed(app):
    conn = app.state.cr.conn
    db.upsert_account(
        conn, "a@x.com", "x.com", "", "mb1", 1, "success",
        session_key="sk-ant-a", proxy="socks5://p:1",
    )
    conn.commit()


def _client(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    c = TestClient(app)
    c.post("/api/login", json={"password": "pw"})
    return app, c


def test_account_check_404_on_missing_account(tmp_path):
    _, c = _client(tmp_path)
    r = c.post("/api/accounts/nobody@x.com/check")
    assert r.status_code == 404


def test_account_check_alive(tmp_path):
    app, c = _client(tmp_path)
    with mock.patch("server.app.check_session", return_value=("alive", "有效")):
        r = c.post("/api/accounts/a@x.com/check")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "alive"
    assert body["detail"] == "有效"
    assert body["checked_at"]

    row = db.get_account(app.state.cr.conn, "a@x.com")
    assert row["check_status"] == "alive"
    assert row["checked_at"] == body["checked_at"]


def test_account_check_requires_auth(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    c = TestClient(app)  # 未登录
    assert c.post("/api/accounts/a@x.com/check").status_code == 401
