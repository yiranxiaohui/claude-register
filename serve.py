"""启动 Web 管理面板。"""
from __future__ import annotations

import os
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
    # 容器内由 Nginx 统一监听面板公开端口，Uvicorn 只监听内部
    # 端口；本地直接运行时未设该变量，仍使用 config.yaml 的 panel.port。
    internal_port = os.environ.get("CLAUDE_REGISTER_INTERNAL_PORT")
    port = int(internal_port or cfg.panel_port)
    host = "127.0.0.1" if internal_port else "0.0.0.0"
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
