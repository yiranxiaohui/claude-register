import inspect
import types

import pytest

from claude_register import browser, flow
from claude_register.browser import parse_proxy


def test_empty_means_direct():
    assert parse_proxy("") is None
    assert parse_proxy("   ") is None
    assert parse_proxy(None) is None


def test_http_no_auth():
    assert parse_proxy("http://1.2.3.4:8080") == {"server": "http://1.2.3.4:8080"}


def test_socks5_with_auth():
    assert parse_proxy("socks5://user:pass@proxy.example.com:1080") == {
        "server": "socks5://proxy.example.com:1080",
        "username": "user",
        "password": "pass",
    }


def test_auth_percent_decoded():
    assert parse_proxy("http://u%40x:p%23w@h:8080") == {
        "server": "http://h:8080",
        "username": "u@x",
        "password": "p#w",
    }


@pytest.mark.parametrize("bad", [
    "1.2.3.4:8080",          # 无 scheme
    "http://:8080",          # 无 host
    "http://host",           # 无 port
    "http://host:abc",       # 端口非数字
    "://",                   # 乱码
])
def test_invalid_raises(bad):
    with pytest.raises(ValueError):
        parse_proxy(bad)


def test_browser_session_accepts_proxy():
    sig = inspect.signature(browser.browser_session)
    assert "proxy" in sig.parameters


def test_run_browser_accepts_proxy():
    sig = inspect.signature(flow.run_browser)
    assert "proxy" in sig.parameters


def test_browser_session_invalid_proxy_raises_before_launch():
    with pytest.raises(ValueError):
        with browser.browser_session(proxy="not-a-proxy"):
            pass


def test_run_fails_fast_on_invalid_proxy_before_mailbox(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("prepare_mailbox 不应该在代理校验失败前被调用")

    monkeypatch.setattr(flow, "prepare_mailbox", _boom)

    config = types.SimpleNamespace(
        anymail_base_url="",
        anymail_api_key="",
        anymail_domain="",
        register_code_regex="",
        anymail_expires_hours=0,
        register_login_timeout=120.0,
        register_auto_login=True,
        register_proxy="not-a-proxy",
    )

    with pytest.raises(ValueError):
        flow.run(config=config)
