"""应用级单例装配：DB 连接、Config 路径、Runner。"""
from __future__ import annotations

import secrets
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
        # 首次启动生成随机会话密钥并持久化（data_dir 已 gitignore，不会被提交）。
        secret_path = self.data_dir / "secret.key"
        if secret_path.exists():
            self.secret = secret_path.read_text(encoding="utf-8").strip()
        else:
            self.secret = secrets.token_hex(32)
            secret_path.write_text(self.secret, encoding="utf-8")

    def config(self) -> Config:
        return load_config(self.config_path)
