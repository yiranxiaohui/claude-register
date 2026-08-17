import threading
import pytest
from server.takeover import TakeoverManager, TakeoverBusy, TakeoverError


def _now():
    return "2026-07-30T00:00:00Z"


class FakeProc:
    def __init__(self, argv):
        self.argv = argv
        self.terminated = False
    def poll(self):
        return None
    def terminate(self):
        self.terminated = True
    def kill(self):
        self.terminated = True
    def wait(self, timeout=None):
        return 0


class FakeLauncher:
    def __init__(self):
        self.spawned = []
    def spawn(self, argv):
        p = FakeProc(argv)
        self.spawned.append(p)
        return p


class FakeBrowser:
    def __init__(self):
        self.closed = False
        self.owner_thread = threading.get_ident()
        self.relogin_thread = None
        self.relogin_kwargs = None
    def close(self):
        self.closed = True
    def relogin(self, **kwargs):
        self.relogin_thread = threading.get_ident()
        self.relogin_kwargs = kwargs
        return "sk-new"


def _mgr(**kw):
    return TakeoverManager(
        now_fn=_now,
        launcher=kw.pop("launcher", FakeLauncher()),
        browser_fn=kw.pop("browser_fn", lambda **k: FakeBrowser()),
        wait_display_fn=kw.pop("wait_display_fn", lambda display: None),
        wait_web_fn=kw.pop("wait_web_fn", lambda host, port: None),
        **kw,
    )


def test_start_status_stop_sequence():
    launcher = FakeLauncher()
    browsers = []
    def bf(**k):
        b = FakeBrowser(); browsers.append(b); return b
    m = _mgr(launcher=launcher, browser_fn=bf)

    info = m.start(email="a@x.com", session_key="sk", proxy="", idle_timeout_s=999)
    assert info["email"] == "a@x.com"
    assert m.status() == {"running": True, "email": "a@x.com", "started_at": "2026-07-30T00:00:00Z"}
    argv0 = [p.argv[0] for p in launcher.spawned]
    assert argv0 == ["xpra"]
    xpra_argv = launcher.spawned[0].argv
    # 关键稳定性/安全参数：localhost + 面板鉴权、无传统 RFB、自动保活、双向剪贴板。
    assert "start-desktop" in xpra_argv
    assert "--bind-tcp=127.0.0.1:14500,auth=none" in xpra_argv
    xvfb_arg = next(arg for arg in xpra_argv if arg.startswith("--xvfb="))
    assert "-nolisten tcp" in xvfb_arg and "-ac" in xvfb_arg
    assert "--rfb-upgrade=0" in xpra_argv
    assert "--clipboard=yes" in xpra_argv
    assert "--clipboard-direction=both" in xpra_argv
    assert "--pings=5" in xpra_argv
    assert len(browsers) == 1

    m.stop()
    assert m.status()["running"] is False
    assert browsers[0].closed is True
    assert all(p.terminated for p in launcher.spawned)


def test_second_start_while_running_raises_busy():
    m = _mgr()
    m.start(email="a@x.com", session_key="sk", idle_timeout_s=999)
    with pytest.raises(TakeoverBusy):
        m.start(email="b@x.com", session_key="sk2", idle_timeout_s=999)
    m.stop()


def test_start_rolls_back_when_browser_fails():
    launcher = FakeLauncher()
    def bad_browser(**k):
        raise RuntimeError("boom")
    m = _mgr(launcher=launcher, browser_fn=bad_browser)
    with pytest.raises(TakeoverError):
        m.start(email="a@x.com", session_key="sk", idle_timeout_s=999)
    assert all(p.terminated for p in launcher.spawned)
    assert m.status()["running"] is False


def test_stop_is_idempotent():
    m = _mgr()
    m.stop()
    assert m.status()["running"] is False


def test_relogin_runs_on_browser_owner_thread():
    browsers = []

    def bf(**kwargs):
        browser = FakeBrowser()
        browsers.append(browser)
        return browser

    m = _mgr(browser_fn=bf)
    m.start(email="a@x.com", session_key="sk", idle_timeout_s=999)
    try:
        session_key = m.relogin(
            email="a@x.com",
            mail_base_url="https://mail.test",
            mail_api_key="ak_child",
            login_timeout=120,
        )
    finally:
        m.stop()

    assert session_key == "sk-new"
    assert browsers[0].relogin_thread == browsers[0].owner_thread
    assert browsers[0].relogin_kwargs["email"] == "a@x.com"


def test_relogin_requires_active_takeover():
    m = _mgr()
    with pytest.raises(TakeoverError, match="没有活动"):
        m.relogin(
            email="a@x.com",
            mail_base_url="https://mail.test",
            mail_api_key="ak_child",
            login_timeout=120,
        )


def test_browser_lifecycle_stays_on_one_thread_across_request_workers():
    """启动和停止可来自不同 API 工作线程，但 Playwright 必须留在同一线程。"""
    browsers = []
    start_errors = []

    class ThreadBoundBrowser:
        def __init__(self):
            self.owner_thread = threading.get_ident()
            self.close_thread = None
            self.closed = False

        def close(self):
            self.close_thread = threading.get_ident()
            if self.close_thread != self.owner_thread:
                raise RuntimeError("browser closed from a different thread")
            self.closed = True

    def browser_fn(**kwargs):
        browser = ThreadBoundBrowser()
        browsers.append(browser)
        return browser

    m = _mgr(browser_fn=browser_fn)

    # 连续两轮模拟异步 start 路由的 asyncio.to_thread 工作线程；stop 则由
    # 当前线程调用。旧实现首轮跨线程关闭失败，第二轮会撞上残留的事件循环。
    for cycle in (1, 2):
        def start_from_worker():
            try:
                m.start(
                    email=f"a{cycle}@x.com", session_key="sk", idle_timeout_s=999,
                )
            except Exception as exc:  # pragma: no cover - asserted below
                start_errors.append(exc)

        start_thread = threading.Thread(target=start_from_worker)
        start_thread.start()
        start_thread.join(timeout=5)

        assert not start_thread.is_alive()
        assert start_errors == []
        m.stop()

    assert len(browsers) == 2
    assert all(browser.closed for browser in browsers)
    assert all(
        browser.close_thread == browser.owner_thread for browser in browsers
    )


def test_idle_timeout_auto_stops_without_heartbeat():
    m = _mgr()
    m.start(email="a@x.com", session_key="sk", idle_timeout_s=0.05)
    for _ in range(50):
        if not m.status()["running"]:
            break
        threading.Event().wait(0.02)
    assert m.status()["running"] is False


def test_heartbeat_extends_idle_timeout():
    m = _mgr()
    m.start(email="a@x.com", session_key="sk", idle_timeout_s=0.08)
    threading.Event().wait(0.05)
    m.touch()
    threading.Event().wait(0.05)
    assert m.status()["running"] is True

    for _ in range(20):
        if not m.status()["running"]:
            break
        threading.Event().wait(0.02)
    assert m.status()["running"] is False


def test_heartbeat_requires_active_session():
    m = _mgr()
    with pytest.raises(TakeoverError, match="没有活动"):
        m.touch()


def test_wait_x_socket_times_out_fast(tmp_path):
    from server.takeover_browser import wait_x_socket
    import pytest
    with pytest.raises(TimeoutError):
        wait_x_socket(":100", timeout=0.2, poll=0.02, sock_dir=str(tmp_path))


def test_wait_x_socket_succeeds_when_present(tmp_path):
    from server.takeover_browser import wait_x_socket
    (tmp_path / "X100").write_text("")  # 伪造 X socket 文件
    wait_x_socket(":100", timeout=0.5, poll=0.02, sock_dir=str(tmp_path))  # 不抛即通过


def test_wait_tcp_port_retries_until_xpra_is_ready():
    from server.takeover_browser import wait_tcp_port

    attempts = []

    class FakeConnection:
        def close(self):
            pass

    def connect(address, timeout):
        attempts.append((address, timeout))
        if len(attempts) < 3:
            raise ConnectionRefusedError
        return FakeConnection()

    wait_tcp_port("127.0.0.1", 14500, timeout=0.2, poll=0.01, connect_fn=connect)
    assert len(attempts) == 3


def test_start_rolls_back_when_xpra_web_port_never_opens():
    launcher = FakeLauncher()

    def fail_wait(host, port):
        raise TimeoutError(f"{host}:{port}")

    m = _mgr(launcher=launcher, wait_web_fn=fail_wait)
    with pytest.raises(TakeoverError, match="14500"):
        m.start(email="a@x.com", session_key="sk", idle_timeout_s=999)
    assert all(p.terminated for p in launcher.spawned)
