"""账号导出：/api/accounts 携带 text 导出块、/api/accounts/export 全量下载。"""
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


TEXT_A = (
    "email：a@x.com\n"
    "sessionkey：sk-ant-a\n"
    "proxy：socks5://p:1\n"
    "mailUrl：https://mail\n"
    "mailKey：mk-a"
)
TEXT_B = (
    "email：b@x.com\n"
    "sessionkey：sk-ant-b\n"
    "proxy：\n"
    "mailUrl：\n"
    "mailKey："
)


def test_accounts_rows_include_export_text(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    c = TestClient(app)
    c.post("/api/login", json={"password": "pw"})
    rows = c.get("/api/accounts").json()
    by_email = {r["email"]: r for r in rows}
    assert by_email["a@x.com"]["text"] == TEXT_A
    assert by_email["b@x.com"]["text"] == TEXT_B


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
    blocks = r.text.strip().split("\n\n")
    assert TEXT_A in blocks
    assert TEXT_B in blocks
    assert len(blocks) == 2
