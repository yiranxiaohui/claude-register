"""应用级单例装配：DB 连接、Config 路径、Runner。"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from server import db
from server.config_store import Config, load_config
from server.runner import Runner


def default_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AppState:
    def __init__(self, data_dir: Path, config_path: Path, now_fn=default_now):
        self.data_dir = Path(data_dir)
        self.config_path = Path(config_path)
        self.now_fn = now_fn
        self.conn = db.init_db(self.data_dir / "claude-register.db")
        db.mark_stale_running_as_failed(self.conn)  # 重启清理残留 running
        self.runner = Runner(self.conn, self.data_dir, now_fn)
        self.secret = "claude-register-panel"

    def config(self) -> Config:
        return load_config(self.config_path)
