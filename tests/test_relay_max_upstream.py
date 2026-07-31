"""接管路径放宽中继上游并发：max_upstream 从 build_camoufox_kwargs 一路透传。

背景：中继槽位是整条隧道生命周期持有，注册流程短连接够用；接管是交互式
浏览，claude.ai 的 HTTP/2 复用 + SSE 长连接会把默认 3 个槽位钉死，其余
请求全部排队 30s 超时。接管路径需要更宽的并发闸门。
"""
from __future__ import annotations

import claude_register.browser as browser_mod
from server import takeover_browser


class _FakeRelay:
    captured: dict = {}

    def __init__(self, url, *, on_error=None, max_upstream=None):
        _FakeRelay.captured = {"url": url, "max_upstream": max_upstream}
        self.local_url = "socks5://127.0.0.1:1"

    def start(self):
        return self

    def exit_ip(self):
        return None

    def stop(self):
        pass


def test_build_kwargs_passes_max_upstream(monkeypatch):
    monkeypatch.setattr(browser_mod, "SocksRelay", _FakeRelay)
    kwargs, relay, _ = browser_mod.build_camoufox_kwargs(
        "socks5://u:p@h:1080", max_upstream=16
    )
    assert _FakeRelay.captured["max_upstream"] == 16
    assert kwargs["proxy"]["server"] == "socks5://127.0.0.1:1"


def test_build_kwargs_default_keeps_none(monkeypatch):
    """注册路径不传 → None，让 SocksRelay 自己取默认/环境变量。"""
    monkeypatch.setattr(browser_mod, "SocksRelay", _FakeRelay)
    browser_mod.build_camoufox_kwargs("socks5://u:p@h:1080")
    assert _FakeRelay.captured["max_upstream"] is None


def test_takeover_max_upstream_default_and_env(monkeypatch):
    monkeypatch.delenv("TAKEOVER_MAX_UPSTREAM", raising=False)
    assert takeover_browser.takeover_max_upstream() == 16
    monkeypatch.setenv("TAKEOVER_MAX_UPSTREAM", "8")
    assert takeover_browser.takeover_max_upstream() == 8
    monkeypatch.setenv("TAKEOVER_MAX_UPSTREAM", "junk")
    assert takeover_browser.takeover_max_upstream() == 16
