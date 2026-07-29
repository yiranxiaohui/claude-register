"""启动 Web 管理面板。"""
from __future__ import annotations

from pathlib import Path

import uvicorn

from server.app import create_app
from server.config_store import load_config

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
CONFIG_PATH = ROOT / "config.yaml"

app = create_app(data_dir=DATA_DIR, config_path=CONFIG_PATH)


def main() -> None:
    cfg = load_config(CONFIG_PATH)
    if not cfg.panel_password:
        print("⚠️  未设置面板密码：仅开放配置接口（/api/config），其余接口全部 401。"
              "请尽快在设置页设定密码后再使用。", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=cfg.panel_port)


if __name__ == "__main__":
    main()
