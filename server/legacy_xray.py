"""内嵌 Xray 已移除后的一次性兼容迁移。

旧版本把注册代理设为 Xray 的 loopback 入口 ``socks5h://127.0.0.1:10808``，
Xray 再把 TCP 原样转发到 ``data/xray/config.json`` 里唯一的 SOCKS5 上游。
镜像不再带 Xray 后该端口无人监听，因此启动时把注册代理改回那个上游，
出口保持不变。读不到可迁移的 SOCKS5 上游时保持原值（注册会连接失败），
宁可报错也不悄悄改成直连。
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

from claude_register.console import log
from server.config_store import load_config, save_config

LEGACY_XRAY_PROXY = "socks5h://127.0.0.1:10808"


def _upstream_from_xray_config(path: Path) -> str | None:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
        outbound = config["outbounds"][0]
        if outbound.get("protocol") != "socks":
            return None
        server = outbound["settings"]["servers"][0]
        host = str(server["address"])
        port = int(server["port"])
        if not host or not 1 <= port <= 65535:
            return None
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        auth = ""
        users = server.get("users") or []
        if users:
            user = quote(str(users[0].get("user", "")), safe="")
            password = quote(str(users[0].get("pass", "")), safe="")
            auth = f"{user}:{password}@"
        return f"socks5h://{auth}{host}:{port}"
    except (OSError, KeyError, IndexError, TypeError, ValueError, AttributeError):
        return None


def migrate_register_proxy(config_path: Path, data_dir: Path) -> bool:
    """注册代理仍指向旧 Xray 入口时改回其 SOCKS5 上游；返回是否改写了配置。"""
    if load_config(config_path).register_proxy != LEGACY_XRAY_PROXY:
        return False
    upstream = _upstream_from_xray_config(Path(data_dir) / "xray" / "config.json")
    if upstream is None:
        log("注册代理仍指向已移除的内嵌 Xray（127.0.0.1:10808），"
            "且未找到可迁移的 SOCKS5 上游，请在设置页重新填写注册代理。")
        return False
    save_config(config_path, {"register_proxy": upstream})
    log("内嵌 Xray 已移除：注册代理已改回原 SOCKS5 上游。")
    return True
