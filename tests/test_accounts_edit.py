"""账号编辑/删除：PATCH /api/accounts/{email} 与 DELETE /api/accounts/{email}。"""
from __future__ import annotations

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
        password="pw-a", session_key="sk-ant-a", proxy="socks5://old:1",
    )
    conn.commit()


def _client(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    c = TestClient(app)
    c.post("/api/login", json={"password": "pw"})
    return app, c


def test_patch_updates_fields(tmp_path):
    app, c = _client(tmp_path)
    r = c.patch("/api/accounts/a@x.com", json={
        "proxy": "socks5://new:2", "display_name": "备注",
    })
    assert r.status_code == 200
    row = db.get_account(app.state.cr.conn, "a@x.com")
    assert row["proxy"] == "socks5://new:2"
    assert row["display_name"] == "备注"
    # 未提交的字段保持不变
    assert row["session_key"] == "sk-ant-a"
    assert row["password"] == "pw-a"
    # 响应带最新行 + 导出文本
    assert r.json()["proxy"] == "socks5://new:2"
    assert "text" in r.json()


def test_patch_can_clear_field(tmp_path):
    app, c = _client(tmp_path)
    r = c.patch("/api/accounts/a@x.com", json={"proxy": ""})
    assert r.status_code == 200
    assert db.get_account(app.state.cr.conn, "a@x.com")["proxy"] == ""


def test_patch_ignores_non_editable_and_requires_known_field(tmp_path):
    app, c = _client(tmp_path)
    # email/status 等不可改；全是未知字段时 400
    r = c.patch("/api/accounts/a@x.com", json={"email": "b@x.com", "status": "failed"})
    assert r.status_code == 400
    row = db.get_account(app.state.cr.conn, "a@x.com")
    assert row is not None and row["status"] == "success"


def test_patch_404_on_missing_account(tmp_path):
    _, c = _client(tmp_path)
    assert c.patch("/api/accounts/nope@x.com", json={"proxy": "x"}).status_code == 404


def test_delete_account(tmp_path):
    app, c = _client(tmp_path)
    r = c.request("DELETE", "/api/accounts/a@x.com")
    assert r.status_code == 200
    assert db.get_account(app.state.cr.conn, "a@x.com") is None
    assert c.request("DELETE", "/api/accounts/a@x.com").status_code == 404


def test_edit_requires_auth(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    c = TestClient(app)  # 未登录
    assert c.patch("/api/accounts/a@x.com", json={"proxy": "x"}).status_code == 401
    assert c.request("DELETE", "/api/accounts/a@x.com").status_code == 401
