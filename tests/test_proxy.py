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


def test_run_fails_fast_on_oversized_proxy_credentials_before_mailbox(monkeypatch):
    """凭据超长同样要在建邮箱之前挡住。

    parse_proxy 只看格式，凭据长度是中继那层才发现的——但发现得太晚就白建了
    一个 AnyMail 邮箱。校验要覆盖『这个代理最终能不能用』，不只是『URL 长得对不对』。
    """
    def _boom(*args, **kwargs):
        raise AssertionError("prepare_mailbox 不应该在代理校验失败前被调用")

    monkeypatch.setattr(flow, "prepare_mailbox", _boom)

    config = types.SimpleNamespace(
        anymail_base_url="https://mail.test",
        anymail_api_key="ak_test",
        anymail_domain="mail.test",
        register_code_regex="",
        anymail_expires_hours=0,
        register_login_timeout=120.0,
        register_auto_login=True,
        register_proxy="socks5://" + "u" * 300 + ":p@h:1080",
    )

    # 必须是「凭据过长」这个错，不能是别的 ValueError 顺手把测试染绿了
    with pytest.raises(ValueError, match="255"):
        flow.run(config=config)


@pytest.mark.parametrize("url", [
    "socks4://u:p@h:1080",
    "socks4a://u:p@h:1080",
])
def test_socks4_with_credentials_rejected(url):
    """SOCKS4 没有用户名密码认证这回事，Playwright 会把凭据默默丢掉。
    用户以为自己在认证，实际裸连——明确拒绝好过静默降级。"""
    with pytest.raises(ValueError):
        parse_proxy(url)


def test_relay_errors_from_other_threads_reach_the_log_sink(monkeypatch):
    """中继的报错发生在它自己的线程里，也必须落进当前的日志 sink。

    console 的 sink 是 ContextVar，新线程起来时上下文是空的——不处理的话，
    中继的报错会直接打到 stdout，网页端的 log.txt 里一个字都看不到，
    而这恰恰是代理出问题时最该看到的信息。
    """
    import threading

    from claude_register import console

    captured = []
    on_errors = []

    class Recording:
        def __init__(self, upstream_url, **kwargs):
            on_errors.append(kwargs["on_error"])
            self.local_url = "socks5://127.0.0.1:51234"

        def start(self):
            return self

        def exit_ip(self):
            return None

        def stop(self):
            pass

    monkeypatch.setattr(browser, "SocksRelay", Recording)
    monkeypatch.setattr(browser, "Camoufox", _fake_camoufox({}))

    token = console.set_sink(captured.append)
    try:
        with browser.browser_session(proxy="socks5://a:b@up.example:1080"):
            # 模拟中继在自己的 handler 线程里报错
            t = threading.Thread(target=lambda: on_errors[0]("上游握手失败"))
            t.start()
            t.join()
    finally:
        console.reset_sink(token)

    assert any("上游握手失败" in line for line in captured), (
        f"中继报错没进 sink，实际收到：{captured}"
    )


@pytest.mark.parametrize("bad", [
    "socks5://alice:s3cret@host",          # 缺端口
    "trojan://alice:s3cret@h:443",         # 不支持的协议
    "socks4://alice:s3cret@h:1080",        # socks4 带凭据
    "http://alice:s3cret@host:abc",        # 端口非数字
])
def test_proxy_errors_do_not_leak_credentials(bad):
    """报错信息不能带密码。

    这些异常会被 runner 写进 output/runs/<id>/log.txt，而那个文件网页端可读。
    配置里的代理密码不该因为一次填错就落进日志。
    """
    with pytest.raises(ValueError) as exc:
        parse_proxy(bad)
    assert "s3cret" not in str(exc.value), f"报错泄露了密码：{exc.value}"


def test_relay_errors_from_concurrent_threads_all_reach_sink(monkeypatch):
    """多个 handler 线程同时报错时，每一条都要进 sink。

    contextvars.Context 不可重入——同一个 Context 对象被两个线程同时 run 会抛
    "is already entered"。而中继报错最密集的时刻恰恰是撞上游并发限额的时候，
    也就是必然并发的场景。
    """
    import threading
    import time

    from claude_register import console

    captured = []
    lock = threading.Lock()
    on_errors = []

    class Recording:
        def __init__(self, upstream_url, **kwargs):
            on_errors.append(kwargs["on_error"])
            self.local_url = "socks5://127.0.0.1:51234"

        def start(self):
            return self

        def exit_ip(self):
            return None

        def stop(self):
            pass

    inside = threading.Event()
    release = threading.Event()

    def sink(msg):
        # 只卡中继的报错：browser_session 启动时自己也会打几行日志，那几行
        # 走的是主线程，若一并卡住会把闸门提前用掉，几个报错线程就撞不上了。
        # 第一个进来的报错线程停在这里不走，其余线程必然在它还占着 Context 时
        # 尝试进入——不这么卡，它们只会依次进出，永远撞不上 "is already entered"。
        if "并发错误" in msg and not inside.is_set():
            inside.set()
            release.wait(timeout=3)
        with lock:
            captured.append(msg)

    monkeypatch.setattr(browser, "SocksRelay", Recording)
    monkeypatch.setattr(browser, "Camoufox", _fake_camoufox({}))

    failures = []

    def report(i):
        try:
            on_errors[0](f"并发错误{i}")
        except Exception as exc:  # noqa: BLE001
            failures.append(repr(exc))

    token = console.set_sink(sink)
    try:
        with browser.browser_session(proxy="socks5://a:b@up.example:1080"):
            ts = [threading.Thread(target=report, args=(i,)) for i in range(5)]
            for t in ts:
                t.start()
            inside.wait(timeout=3)  # 等第一个线程占住 Context
            time.sleep(0.2)  # 给其余线程时间撞上去
            release.set()
            for t in ts:
                t.join(timeout=5)
    finally:
        console.reset_sink(token)

    assert not failures, f"并发回调不能抛异常：{failures}"
    for i in range(5):
        assert any(f"并发错误{i}" in line for line in captured), f"丢了第 {i} 条"


def test_socks4_without_credentials_still_ok():
    """无凭据的 socks4 本来就能用，别误伤。"""
    assert parse_proxy("socks4://h:1080") == {"server": "socks4://h:1080"}


def test_relay_gets_normalized_url_not_raw_string(monkeypatch):
    """传给中继的是归一化后的地址，不是配置里的原始字符串。

    原始串可能带首尾空白、或者用 socks5h:// 这种 scheme。parse_proxy 都处理掉了，
    中继却拿的是原文——两边解析结果不一致，排查起来是纯纯的坑。
    """
    seen = {}
    monkeypatch.setattr(browser, "SocksRelay", _fake_relay("203.0.113.9"))
    monkeypatch.setattr(browser, "Camoufox", _fake_camoufox({}))

    class Recording:
        def __init__(self, upstream_url, **kwargs):
            seen["upstream"] = upstream_url
            self.local_url = "socks5://127.0.0.1:51234"

        def start(self):
            return self

        def exit_ip(self):
            return None

        def stop(self):
            pass

    monkeypatch.setattr(browser, "SocksRelay", Recording)

    with browser.browser_session(proxy="  socks5h://alice:s3cret@up.example:1080  "):
        pass

    assert seen["upstream"] == "socks5://alice:s3cret@up.example:1080"


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


def test_relay_launch_failure_blames_proxy_not_missing_binary(monkeypatch):
    """中继起不来（比如上游端口不通）时，报错必须指向代理。

    原来这类失败会一路漏到 Camoufox 那层的兜底提示，用户看到的是
    『请先运行 uv run camoufox fetch，并确认已安装 Xvfb』——完全指错方向。
    """
    class BoomRelay:
        def __init__(self, upstream_url, **kwargs):
            pass

        def start(self):
            raise OSError("上游端口不通")

    def _no_launch(**kwargs):
        raise AssertionError("中继起不来就不该再去启动浏览器")

    monkeypatch.setattr(browser, "SocksRelay", BoomRelay)
    monkeypatch.setattr(browser, "Camoufox", _no_launch)

    with pytest.raises(RuntimeError, match="代理"):
        with browser.browser_session(proxy="socks5://a:b@up.example:1080"):
            pass


def test_relay_launch_failure_does_not_mention_camoufox_fetch(monkeypatch):
    """具体确认那句误导性提示不会出现。"""
    class BoomRelay:
        def __init__(self, upstream_url, **kwargs):
            pass

        def start(self):
            raise OSError("上游端口不通")

    monkeypatch.setattr(browser, "SocksRelay", BoomRelay)
    monkeypatch.setattr(browser, "Camoufox", lambda **kw: None)

    with pytest.raises(RuntimeError) as exc:
        with browser.browser_session(proxy="socks5://a:b@up.example:1080"):
            pass

    assert "camoufox fetch" not in str(exc.value)
    assert "Xvfb" not in str(exc.value)


def test_build_kwargs_no_proxy():
    from claude_register.browser import build_camoufox_kwargs
    kwargs, relay, geoip = build_camoufox_kwargs(None)
    assert "proxy" not in kwargs
    assert relay is None
    assert geoip is True


def test_build_kwargs_plain_proxy():
    from claude_register.browser import build_camoufox_kwargs
    kwargs, relay, geoip = build_camoufox_kwargs("http://1.2.3.4:8080")
    assert kwargs["proxy"] == {"server": "http://1.2.3.4:8080"}
    assert relay is None          # 无认证不需要中继
    assert geoip is True
