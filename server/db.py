"""SQLite 数据访问：runs / accounts。纯数据层，无业务逻辑。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT, domain TEXT, status TEXT,
  started_at TEXT, finished_at TEXT, output_dir TEXT
);
CREATE TABLE IF NOT EXISTS accounts (
  email TEXT UNIQUE, domain TEXT, created_at TEXT, expires_at TEXT,
  mailbox_id TEXT, last_run_id INTEGER, status TEXT,
  password TEXT, session_key TEXT, proxy TEXT, display_name TEXT,
  mail_key TEXT, mail_base_url TEXT
);
"""

_ACCOUNT_EXTRA_COLS = (
    ("password", "TEXT"),
    ("session_key", "TEXT"),
    ("proxy", "TEXT"),
    ("display_name", "TEXT"),
    ("mail_key", "TEXT"),
    ("mail_base_url", "TEXT"),
)


def _migrate_accounts(conn: sqlite3.Connection) -> None:
    """旧库补列：password / session_key / proxy / display_name / mail_key / mail_base_url。"""
    existing = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(accounts)").fetchall()
    }
    for col, typ in _ACCOUNT_EXTRA_COLS:
        if col not in existing:
            conn.execute(f"ALTER TABLE accounts ADD COLUMN {col} {typ}")


def init_db(path: Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    _migrate_accounts(conn)
    conn.commit()
    return conn


def create_run(conn, email, domain, output_dir, now) -> int:
    cur = conn.execute(
        "INSERT INTO runs(email,domain,status,started_at,output_dir) "
        "VALUES(?,?,'running',?,?)",
        (email, domain, now, output_dir),
    )
    conn.commit()
    return cur.lastrowid


def finish_run(conn, run_id, status, now) -> None:
    conn.execute("UPDATE runs SET status=?, finished_at=? WHERE id=?",
                 (status, now, run_id))
    conn.commit()


def _row(r) -> dict | None:
    return dict(r) if r is not None else None


def list_runs(conn, limit=50, offset=0) -> list[dict]:
    rows = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ? OFFSET ?",
                        (limit, offset)).fetchall()
    return [dict(r) for r in rows]


def get_run(conn, run_id) -> dict | None:
    return _row(conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())


def active_run(conn) -> dict | None:
    return _row(conn.execute(
        "SELECT * FROM runs WHERE status='running' ORDER BY id DESC LIMIT 1").fetchone())


def mark_stale_running_as_failed(conn) -> int:
    cur = conn.execute("UPDATE runs SET status='failed' WHERE status='running'")
    conn.commit()
    return cur.rowcount


def upsert_account(
    conn,
    email,
    domain,
    expires_at,
    mailbox_id,
    last_run_id,
    status,
    *,
    password: str = "",
    session_key: str = "",
    proxy: str = "",
    display_name: str = "",
    created_at: str = "",
    mail_key: str = "",
    mail_base_url: str = "",
) -> None:
    conn.execute(
        "INSERT INTO accounts(email,domain,created_at,expires_at,mailbox_id,last_run_id,status,"
        "password,session_key,proxy,display_name,mail_key,mail_base_url) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(email) DO UPDATE SET domain=excluded.domain, "
        "expires_at=excluded.expires_at, mailbox_id=excluded.mailbox_id, "
        "last_run_id=excluded.last_run_id, status=excluded.status, "
        "password=excluded.password, session_key=excluded.session_key, "
        "proxy=excluded.proxy, display_name=excluded.display_name, "
        "mail_key=excluded.mail_key, mail_base_url=excluded.mail_base_url",
        (
            email,
            domain,
            created_at or "",
            expires_at,
            mailbox_id,
            last_run_id,
            status,
            password or "",
            session_key or "",
            proxy or "",
            display_name or "",
            mail_key or "",
            mail_base_url or "",
        ),
    )
    conn.commit()


def list_accounts(conn) -> list[dict]:
    rows = conn.execute("SELECT * FROM accounts ORDER BY rowid DESC").fetchall()
    return [dict(r) for r in rows]


def get_account(conn, email) -> dict | None:
    row = conn.execute("SELECT * FROM accounts WHERE email=?", (email,)).fetchone()
    return dict(row) if row else None


# 面板可手工编辑的字段；email/status 等由注册流程维护，不开放
ACCOUNT_EDITABLE_FIELDS = (
    "password", "session_key", "proxy", "display_name", "mail_key", "mail_base_url",
)


def update_account_fields(conn, email, fields: dict) -> bool:
    """仅更新 ACCOUNT_EDITABLE_FIELDS 里的字段，返回是否有行被更新。"""
    sets = {k: str(v or "") for k, v in fields.items() if k in ACCOUNT_EDITABLE_FIELDS}
    if not sets:
        return False
    sql = "UPDATE accounts SET " + ", ".join(f"{k}=?" for k in sets) + " WHERE email=?"
    cur = conn.execute(sql, (*sets.values(), email))
    conn.commit()
    return cur.rowcount > 0


def delete_account(conn, email) -> bool:
    cur = conn.execute("DELETE FROM accounts WHERE email=?", (email,))
    conn.commit()
    return cur.rowcount > 0
