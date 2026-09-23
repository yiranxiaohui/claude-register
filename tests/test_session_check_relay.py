"""check_session 真实链路：curl_cffi → 本地中继 → 带认证 SOCKS5 上游。

不 mock HTTP 客户端：之前的 bug 恰恰出在「测试用 httpx 打桩、生产也用 httpx」，
而生产 httpx 的 TLS 指纹会被 Cloudflare 拦下。这里至少保证真实客户端 + 中继
的拼装是通的，并且目标域名是交给上游解析的（不在本地解析成 fake-ip）。
"""
from __future__ import annotations

import json

import pytest

from claude_register import session_check
from claude_register.session_check import check_session
from tests.test_socks_relay import FakeUpstream


def _http_upstream(status: int, body: str, *, ctype="application/json", extra=""):
    raw = body.encode()
    resp = (
        f"HTTP/1.1 {status} X\r\nContent-Type: {ctype}\r\n{extra}"
        f"Content-Length: {len(raw)}\r\nConnection: close\r\n\r\n"
    ).encode() + raw
    up = FakeUpstream("alice", "s3cret", echo=resp)
    return up


@pytest.fixture
def plain_http(monkeypatch):
    """测试上游不说 TLS，把检测地址换成 http://，其余链路不变。"""
    monkeypatch.setattr(session_check, "ORG_URL", "http://claude.test/api/organizations")


def _proxy(up):
    return f"socks5://alice:s3cret@127.0.0.1:{up.port}"


def test_alive_through_relay_with_remote_dns(plain_http):
    up = _http_upstream(200, json.dumps([{"uuid": "org1"}]))
    try:
        assert check_session("sk-x", _proxy(up)) == ("alive", "有效")
        # 域名原样交给上游：没有在本地被解析成（可能是 fake-ip 的）IP
        assert up.requested == [("claude.test", 80)]
        assert up.auth_attempts == [("alice", "s3cret")]
    finally:
        up.close()


def test_shield_detected_on_real_curl_response(plain_http):
    up = _http_upstream(403, "<html>Just a moment...</html>", ctype="text/html",
                        extra="cf-mitigated: challenge\r\n")
    try:
        status, detail = check_session("sk-x", _proxy(up))
        assert status == "blocked"
    finally:
        up.close()


def test_dead_on_real_curl_response(plain_http):
    up = _http_upstream(401, json.dumps({"error": {"type": "authentication_error"}}))
    try:
        assert check_session("sk-x", _proxy(up))[0] == "dead"
    finally:
        up.close()


def test_bad_upstream_credentials_is_error_not_crash(plain_http):
    up = FakeUpstream("alice", "other")
    try:
        status, detail = check_session("sk-x", _proxy(up))
        assert status == "error"
        assert "s3cret" not in detail
    finally:
        up.close()
