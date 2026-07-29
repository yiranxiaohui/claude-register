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
  mailbox_id TEXT, last_run_id INTEGER, status TEXT
);
"""


def init_db(path: Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
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


def upsert_account(conn, email, domain, expires_at, mailbox_id, last_run_id, status) -> None:
    conn.execute(
        "INSERT INTO accounts(email,domain,created_at,expires_at,mailbox_id,last_run_id,status) "
        "VALUES(?,?,?,?,?,?,?) "
        "ON CONFLICT(email) DO UPDATE SET domain=excluded.domain, "
        "expires_at=excluded.expires_at, mailbox_id=excluded.mailbox_id, "
        "last_run_id=excluded.last_run_id, status=excluded.status",
        (email, domain, "", expires_at, mailbox_id, last_run_id, status),
    )
    conn.commit()


def list_accounts(conn) -> list[dict]:
    rows = conn.execute("SELECT * FROM accounts ORDER BY rowid DESC").fetchall()
    return [dict(r) for r in rows]
