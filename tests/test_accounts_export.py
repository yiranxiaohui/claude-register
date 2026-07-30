"""账号导出：/api/accounts 携带 line 导出行、/api/accounts/export 全量下载。"""
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
        now_fn=lambda: "2026-07-30T00:00:00Z",
    )


def _seed(app):
    conn = app.state.cr.conn
    db.upsert_account(
        conn, "a@x.com", "x.com", "", "mb1", 1, "success",
        password="pw-a", session_key="sk-ant-a", proxy="socks5://p:1",
        mail_key="mk-a", mail_base_url="https://mail",
    )
    db.upsert_account(
        conn, "b@x.com", "x.com", "", "mb2", 2, "success",
        password="pw-b", session_key="sk-ant-b",
    )
    conn.commit()


def test_accounts_rows_include_export_line(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    c = TestClient(app)
    c.post("/api/login", json={"password": "pw"})
    rows = c.get("/api/accounts").json()
    by_email = {r["email"]: r for r in rows}
    assert by_email["a@x.com"]["line"] == "a@x.com----pw-a----sk-ant-a----socks5://p:1----mk-a"
    assert by_email["b@x.com"]["line"] == "b@x.com----pw-b----sk-ant-b--------"


def test_export_requires_auth(tmp_path):
    app = _app(tmp_path)
    c = TestClient(app)
    assert c.get("/api/accounts/export").status_code == 401


def test_export_all_accounts_txt(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    c = TestClient(app)
    c.post("/api/login", json={"password": "pw"})
    r = c.get("/api/accounts/export")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert "attachment" in r.headers.get("content-disposition", "")
    lines = r.text.strip().splitlines()
    assert "a@x.com----pw-a----sk-ant-a----socks5://p:1----mk-a" in lines
    assert "b@x.com----pw-b----sk-ant-b--------" in lines
    assert len(lines) == 2
