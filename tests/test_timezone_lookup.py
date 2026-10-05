"""经代理查出口时区：解析各站点格式、socks5→socks5h、全部失败返回 None。"""
from __future__ import annotations

from curl_cffi import requests as cffi_requests

from claude_register import browser

# conftest 的 autouse fixture 会把 browser.lookup_timezone 换成假实现；
# 模块导入发生在 fixture 之前，这里拿到的是真函数。
lookup_timezone = browser.lookup_timezone


class Resp:
    def __init__(self, status, data):
        self.status_code = status
        self._data = data

    def json(self):
        if isinstance(self._data, Exception):
            raise self._data
        return self._data


def _patch(monkeypatch, responses):
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs["proxy"]))
        r = responses.get(url)
        if isinstance(r, Exception):
            raise r
        return r or Resp(500, {})

    monkeypatch.setattr(cffi_requests, "get", fake_get)
    return calls


def test_socks5_is_resolved_remotely_and_first_hit_wins(monkeypatch):
    calls = _patch(monkeypatch, {"https://ipinfo.io/json": Resp(200, {"timezone": "Asia/Tokyo"})})
    assert lookup_timezone("socks5://127.0.0.1:4000") == "Asia/Tokyo"
    assert calls == [("https://ipinfo.io/json", "socks5h://127.0.0.1:4000")]


def test_falls_through_failures_and_parses_nested_timezone(monkeypatch):
    _patch(monkeypatch, {
        "https://ipinfo.io/json": OSError("blocked"),
        "https://ipapi.co/json/": Resp(429, {}),
        "https://ipwho.is/": Resp(200, {"timezone": {"id": "America/New_York"}}),
    })
    assert lookup_timezone("http://u:p@h:8080") == "America/New_York"


def test_rejects_garbage_and_returns_none(monkeypatch):
    _patch(monkeypatch, {
        "https://ipinfo.io/json": Resp(200, {"timezone": "x; rm -rf /"}),
        "https://ipapi.co/json/": Resp(200, ValueError("not json")),
    })
    assert lookup_timezone("http://h:8080") is None


def test_no_proxy_no_lookup(monkeypatch):
    calls = _patch(monkeypatch, {})
    assert lookup_timezone(None) is None
    assert calls == []
