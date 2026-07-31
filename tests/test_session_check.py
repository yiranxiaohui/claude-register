"""check_session：三态 + CF 盾 + 空 key + 代理传参。"""
from __future__ import annotations

import httpx

from claude_register.session_check import _default_client, check_session

ORG_URL = "https://claude.ai/api/organizations"


def _factory(handler):
    """返回一个 client_factory(proxy)->httpx.Client，用 MockTransport 打桩。
    并把最后一次收到的 proxy 记到 captured 里，供断言。"""
    captured = {}

    def make(proxy=None):
        captured["proxy"] = proxy
        return httpx.Client(transport=httpx.MockTransport(handler))

    return make, captured


def test_alive_200_json():
    make, _ = _factory(lambda req: httpx.Response(200, json=[{"uuid": "org1"}]))
    assert check_session("sk-x", client_factory=make) == ("alive", "有效")


def test_dead_401_json():
    make, _ = _factory(lambda req: httpx.Response(401, json={"error": {"type": "authentication_error"}}))
    status, _ = check_session("sk-x", client_factory=make)
    assert status == "dead"


def test_dead_403_json():
    make, _ = _factory(lambda req: httpx.Response(403, json={"error": {"type": "permission_error"}}))
    status, _ = check_session("sk-x", client_factory=make)
    assert status == "dead"


def test_cf_shield_403_html_is_error():
    def handler(req):
        return httpx.Response(403, headers={"cf-mitigated": "challenge"},
                              text="<!DOCTYPE html><html>Just a moment...</html>")
    make, _ = _factory(handler)
    status, detail = check_session("sk-x", client_factory=make)
    assert status == "error"
    assert "盾" in detail or "cloudflare" in detail.lower()


def test_403_html_without_header_is_error():
    make, _ = _factory(lambda req: httpx.Response(403, text="<html>blocked</html>"))
    status, _ = check_session("sk-x", client_factory=make)
    assert status == "error"


def test_connect_error_is_error():
    def handler(req):
        raise httpx.ConnectError("boom")
    make, _ = _factory(handler)
    status, _ = check_session("sk-x", client_factory=make)
    assert status == "error"


def test_empty_key_no_request():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(200, json=[])

    make, _ = _factory(handler)
    status, detail = check_session("", client_factory=make)
    assert status == "error"
    assert calls["n"] == 0  # 没有发请求


def test_proxy_passed_to_factory():
    make, captured = _factory(lambda req: httpx.Response(200, json=[]))
    check_session("sk-x", proxy="socks5://u:p@1.2.3.4:1080", client_factory=make)
    assert captured["proxy"] == "socks5://u:p@1.2.3.4:1080"


def test_no_proxy_passes_none():
    make, captured = _factory(lambda req: httpx.Response(200, json=[]))
    check_session("sk-x", proxy="", client_factory=make)
    assert captured["proxy"] is None


def test_default_client_constructs():
    """生产工厂 _default_client 能正常构造 httpx.Client（不走网络、不 mock）。"""
    with _default_client("http://1.2.3.4:8080") as client:
        assert isinstance(client, httpx.Client)
    with _default_client(None) as client:
        assert isinstance(client, httpx.Client)
