from server import db


def test_create_and_get_run(tmp_path):
    conn = db.init_db(tmp_path / "t.db")
    rid = db.create_run(conn, "a@x.com", "x.com", "data/runs/1", "2026-07-29T00:00:00Z")
    row = db.get_run(conn, rid)
    assert row["email"] == "a@x.com"
    assert row["status"] == "running"


def test_active_run_and_finish(tmp_path):
    conn = db.init_db(tmp_path / "t.db")
    rid = db.create_run(conn, "a@x.com", "x.com", "d", "2026-07-29T00:00:00Z")
    assert db.active_run(conn)["id"] == rid
    db.finish_run(conn, rid, "success", "2026-07-29T00:01:00Z")
    assert db.active_run(conn) is None
    assert db.get_run(conn, rid)["status"] == "success"


def test_mark_stale(tmp_path):
    conn = db.init_db(tmp_path / "t.db")
    db.create_run(conn, "a@x.com", "x.com", "d", "2026-07-29T00:00:00Z")
    assert db.mark_stale_running_as_failed(conn) == 1
    assert db.active_run(conn) is None


def test_upsert_account_unique(tmp_path):
    conn = db.init_db(tmp_path / "t.db")
    db.upsert_account(conn, "a@x.com", "x.com", None, "m1", 1, "success")
    db.upsert_account(conn, "a@x.com", "x.com", None, "m2", 2, "success")
    rows = db.list_accounts(conn)
    assert len(rows) == 1
    assert rows[0]["mailbox_id"] == "m2"
    assert rows[0]["last_run_id"] == 2


def test_upsert_account_session_fields(tmp_path):
    conn = db.init_db(tmp_path / "t.db")
    db.upsert_account(
        conn,
        "a@x.com",
        "x.com",
        None,
        "m1",
        1,
        "success",
        password="",
        session_key="sk-ant-1",
        proxy="socks5://127.0.0.1:1",
        display_name="Alex",
        created_at="2026-07-30T00:00:00Z",
    )
    row = db.list_accounts(conn)[0]
    assert row["session_key"] == "sk-ant-1"
    assert row["proxy"].startswith("socks5://")
    assert row["display_name"] == "Alex"


def test_upsert_account_mail_key_fields(tmp_path):
    """面板 /api/accounts 从这张表出数据，落库必须带上 mail_key/mail_base_url，
    否则 web 端注册出来的账号永远拿不到子 key。"""
    conn = db.init_db(tmp_path / "t.db")
    db.upsert_account(
        conn,
        "a@x.com",
        "x.com",
        None,
        "m1",
        1,
        "success",
        session_key="sk-ant-1",
        mail_key="ak_child",
        mail_base_url="https://mail.test",
    )
    row = db.list_accounts(conn)[0]
    assert row["mail_key"] == "ak_child"
    assert row["mail_base_url"] == "https://mail.test"


def test_upsert_account_mail_key_defaults_empty(tmp_path):
    conn = db.init_db(tmp_path / "t.db")
    db.upsert_account(conn, "a@x.com", "x.com", None, "m1", 1, "success")
    row = db.list_accounts(conn)[0]
    assert row["mail_key"] == ""
    assert row["mail_base_url"] == ""


def test_migrate_adds_mail_key_columns(tmp_path):
    """旧库只有 session 相关列（无 mail_key/mail_base_url）时 init_db 要补齐。"""
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE accounts (
          email TEXT UNIQUE, domain TEXT, created_at TEXT, expires_at TEXT,
          mailbox_id TEXT, last_run_id INTEGER, status TEXT,
          password TEXT, session_key TEXT, proxy TEXT, display_name TEXT
        );
        """
    )
    conn.commit()
    conn.close()
    conn2 = db.init_db(path)
    cols = {r[1] for r in conn2.execute("PRAGMA table_info(accounts)").fetchall()}
    assert "mail_key" in cols
    assert "mail_base_url" in cols


def test_migrate_adds_session_columns(tmp_path):
    """旧库只有基础列时 init_db 会补 session 相关列。"""
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE accounts (
          email TEXT UNIQUE, domain TEXT, created_at TEXT, expires_at TEXT,
          mailbox_id TEXT, last_run_id INTEGER, status TEXT
        );
        """
    )
    conn.commit()
    conn.close()
    conn2 = db.init_db(path)
    cols = {r[1] for r in conn2.execute("PRAGMA table_info(accounts)").fetchall()}
    assert "session_key" in cols
    assert "proxy" in cols


def test_update_account_check(tmp_path):
    conn = db.init_db(tmp_path / "t.db")
    db.upsert_account(conn, email="a@x.com", domain="x.com", created_at="t0",
                      expires_at=None, mailbox_id="m", last_run_id=1, status="success")
    assert db.update_account_check(conn, "a@x.com", "alive", "2026-07-31T00:00:00Z") is True
    row = db.get_account(conn, "a@x.com")
    assert row["check_status"] == "alive"
    assert row["checked_at"] == "2026-07-31T00:00:00Z"


def test_update_account_check_missing(tmp_path):
    conn = db.init_db(tmp_path / "t.db")
    assert db.update_account_check(conn, "none@x.com", "dead", "t") is False


def test_migrate_adds_check_columns(tmp_path):
    # 老库无新列，init_db 后应补上
    conn = db.init_db(tmp_path / "t.db")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(accounts)").fetchall()}
    assert "check_status" in cols and "checked_at" in cols
