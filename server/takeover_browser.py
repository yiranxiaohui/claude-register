"""接管会话的真实副作用实现：等 X socket 就绪、开注入 Cookie 的 Camoufox。

与 server/takeover.py 分开，好让 TakeoverManager 单测完全不碰 Camoufox / 文件系统。
"""
from __future__ import annotations

import os
import time

from camoufox.sync_api import Camoufox

from claude_register.browser import build_camoufox_kwargs
from claude_register.console import log


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
    kwargs, relay, geoip = build_camoufox_kwargs(proxy or None)
    cm = Camoufox(
        headless=False,
        humanize=True,
        locale="en-US",
        geoip=geoip,
        window=(1280, 900),
        env={"DISPLAY": display},
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
