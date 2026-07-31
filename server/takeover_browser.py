"""接管会话的真实副作用实现：等 X socket 就绪、开注入 Cookie 的 Camoufox。

与 server/takeover.py 分开，好让 TakeoverManager 单测完全不碰 Camoufox / 文件系统。
"""
from __future__ import annotations

import os
import time

from camoufox.sync_api import Camoufox

from claude_register.browser import build_camoufox_kwargs
from claude_register.console import log


# 接管中继的上游并发上限。注册流程用默认 3（匹配机场硬限额、连接短命周转快），
# 但接管是交互式浏览：claude.ai 的 HTTP/2 复用连接、SSE 事件流是几分钟不关的
# 长命连接，槽位按隧道生命周期持有，3 个槽会被前三条长命连接钉死，其余请求
# 全在本地排队 30s 然后超时（表现为满屏「等待上游并发槽位超时」）。
DEFAULT_TAKEOVER_MAX_UPSTREAM = 16


def takeover_max_upstream() -> int:
    raw = os.environ.get("TAKEOVER_MAX_UPSTREAM", "")
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_TAKEOVER_MAX_UPSTREAM
    return value if value > 0 else DEFAULT_TAKEOVER_MAX_UPSTREAM


def wait_x_socket(display: str, timeout: float = 10.0, poll: float = 0.1,
                  sock_dir: str = "/tmp/.X11-unix") -> None:
    """轮询等待 Xvfb 的 UNIX socket 出现（display ":100" → 文件 X100）。"""
    num = display.lstrip(":").split(".")[0]
    path = os.path.join(sock_dir, f"X{num}")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.exists(path):
            return
        time.sleep(poll)
    raise TimeoutError(f"等待 X 显示 {display} 就绪超时（{path} 未出现）")


class _BrowserHandle:
    def __init__(self, cm, relay):
        self._cm = cm
        self._relay = relay

    def close(self):
        try:
            self._cm.__exit__(None, None, None)
        finally:
            if self._relay is not None:
                self._relay.stop()


def open_takeover_browser(*, session_key: str, proxy: str = "", display: str = ":100"):
    """开一个已登录 claude.ai 的 Camoufox（挂在指定 X display 上），返回带 .close() 的句柄。"""
    kwargs, relay, geoip = build_camoufox_kwargs(
        proxy or None, max_upstream=takeover_max_upstream()
    )
    cm = Camoufox(
        headless=False,
        humanize=True,
        locale="en-US",
        geoip=geoip,
        window=(1280, 900),
        virtual_display=display,  # 挂到我们自管的 Xvfb :100，同时保留完整 os.environ
        **kwargs,
    )
    try:
        browser = cm.__enter__()
    except Exception as exc:
        if relay is not None:
            relay.stop()
        raise RuntimeError(
            f"启动接管浏览器失败（{exc}）。请确认已 `uv run camoufox fetch` 且 Xvfb 可用。"
        ) from exc
    try:
        context = browser.new_context(no_viewport=True)
        context.add_cookies([{
            "name": "sessionKey",
            "value": session_key,
            "domain": ".claude.ai",
            "path": "/",
            "secure": True,
            "httpOnly": True,
        }])
        page = context.new_page()
        page.goto("https://claude.ai", wait_until="domcontentloaded", timeout=60_000)
        log("接管浏览器已注入 sessionKey 并打开 claude.ai。")
    except Exception as exc:
        cm.__exit__(None, None, None)
        if relay is not None:
            relay.stop()
        raise RuntimeError(f"注入 sessionKey / 打开 claude.ai 失败（{exc}）。") from exc
    return _BrowserHandle(cm, relay)
