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


def test_socks5h_normalized_to_socks5():
    """socks5h 是 curl 的写法（远端 DNS），Playwright 不认这个 scheme，语义上等价 socks5。"""
    assert parse_proxy("socks5h://h:1080") == {"server": "socks5://h:1080"}


@pytest.mark.parametrize("bad", [
    "ftp://h:21",
    "trojan://h:443",
    "ss://h:8388",
    "socks://h:1080",
])
def test_unknown_scheme_raises(bad):
    """Playwright 的 toJugglerProxyOptions 对不认识的 scheme 会静默降级成 http 代理，
    然后浏览器拿 HTTP CONNECT 去捅一个非 HTTP 端口，卡到 NS_ERROR_NET_TIMEOUT。
    与其让它烂在 60 秒超时里，不如在这里就明确拒绝。"""
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


def test_socks5_with_auth_needs_relay():
    """Firefox 不支持 SOCKS5 认证，带凭据的 socks5 必须走中继。"""
    assert browser.needs_relay(parse_proxy("socks5://u:p@h:1080")) is True


@pytest.mark.parametrize("url", [
    "socks5://h:1080",          # socks5 无凭据，浏览器原生支持
    "http://u:p@h:8080",        # HTTP 代理认证，浏览器原生支持
    "https://u:p@h:8080",
])
def test_no_relay_when_browser_supports_it(url):
    """能直连的就别多起一层中继。"""
    assert browser.needs_relay(parse_proxy(url)) is False


def test_no_relay_without_proxy():
    assert browser.needs_relay(None) is False


def test_socks5_auth_launches_relay_and_passes_local_url(monkeypatch):
    """带认证的 socks5：传给 Camoufox 的必须是中继的免认证本地地址，
    而不是原始带凭据的地址——后者会让浏览器直接抛 authentication 报错。"""
    seen = {}

    class FakeRelay:
        def __init__(self, upstream_url, **kwargs):
            seen["upstream"] = upstream_url
            self.local_url = "socks5://127.0.0.1:51234"
            self.stopped = False

        def start(self):
            return self

        def exit_ip(self):
            return "203.0.113.9"

        def stop(self):
            self.stopped = True
            seen["stopped"] = True

    class FakeCamoufox:
        def __init__(self, **kwargs):
            seen["kwargs"] = kwargs

        def __enter__(self):
            return "BROWSER"

        def __exit__(self, *exc):
            return None

    monkeypatch.setattr(browser, "SocksRelay", FakeRelay)
    monkeypatch.setattr(browser, "Camoufox", FakeCamoufox)

    with browser.browser_session(proxy="socks5://alice:s3cret@up.example:1080") as b:
        assert b == "BROWSER"

    assert seen["upstream"] == "socks5://alice:s3cret@up.example:1080"
    assert seen["kwargs"]["proxy"] == {"server": "socks5://127.0.0.1:51234"}
    assert "username" not in seen["kwargs"]["proxy"], "凭据不能再传给浏览器"
    assert seen["stopped"] is True, "退出后必须关掉中继，否则端口泄漏"


def test_relay_stopped_even_if_body_raises(monkeypatch):
    """调用方 body 抛异常也不能漏掉中继的关闭。"""
    stopped = []

    class FakeRelay:
        def __init__(self, upstream_url, **kwargs):
            self.local_url = "socks5://127.0.0.1:51234"

        def start(self):
            return self

        def exit_ip(self):
            return "203.0.113.9"

        def stop(self):
            stopped.append(True)

    class FakeCamoufox:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return "BROWSER"

        def __exit__(self, *exc):
            return None

    monkeypatch.setattr(browser, "SocksRelay", FakeRelay)
    monkeypatch.setattr(browser, "Camoufox", FakeCamoufox)

    with pytest.raises(RuntimeError, match="boom"):
        with browser.browser_session(proxy="socks5://a:b@up.example:1080"):
            raise RuntimeError("boom")

    assert stopped == [True]


def _fake_relay(exit_ip):
    class FakeRelay:
        def __init__(self, upstream_url, **kwargs):
            self.local_url = "socks5://127.0.0.1:51234"

        def start(self):
            return self

        def exit_ip(self):
            return exit_ip

        def stop(self):
            pass

    return FakeRelay


def _fake_camoufox(seen):
    class FakeCamoufox:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def __enter__(self):
            return "BROWSER"

        def __exit__(self, *exc):
            return None

    return FakeCamoufox


def test_geoip_uses_exit_ip_from_relay(monkeypatch):
    """camoufox 的 geoip=True 会自己发请求探测出口 IP，但它走本地 DNS——
    本地被 fake-ip 污染时会解析出 198.18.x.x，拿去 CONNECT 上游必然失败。
    中继查到的 IP 直接喂给它，绕开那次探测。"""
    seen = {}
    monkeypatch.setattr(browser, "SocksRelay", _fake_relay("203.0.113.9"))
    monkeypatch.setattr(browser, "Camoufox", _fake_camoufox(seen))

    with browser.browser_session(proxy="socks5://a:b@up.example:1080"):
        pass

    assert seen["geoip"] == "203.0.113.9", "应传具体 IP，而不是让 camoufox 自己探测"


def test_geoip_falls_back_to_true_when_exit_ip_unknown(monkeypatch):
    """查不到出口 IP 就退回 geoip=True，让 camoufox 自己试——
    降级，而不是直接放弃启动。"""
    seen = {}
    monkeypatch.setattr(browser, "SocksRelay", _fake_relay(None))
    monkeypatch.setattr(browser, "Camoufox", _fake_camoufox(seen))

    with browser.browser_session(proxy="socks5://a:b@up.example:1080"):
        pass

    assert seen["geoip"] is True
