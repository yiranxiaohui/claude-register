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
    def close(self):
        self.closed = True


def _mgr(**kw):
    return TakeoverManager(
        now_fn=_now,
        launcher=kw.pop("launcher", FakeLauncher()),
        browser_fn=kw.pop("browser_fn", lambda **k: FakeBrowser()),
        wait_display_fn=kw.pop("wait_display_fn", lambda display: None),
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
    assert argv0 == ["Xvnc"]
    xvnc_argv = launcher.spawned[0].argv
    # 关键安全参数：只绑 localhost、鉴权交给面板反代、跳过 STUN 公网探测
    assert "-interface" in xvnc_argv and "127.0.0.1" in xvnc_argv
    assert "-DisableBasicAuth" in xvnc_argv
    assert "-publicIP" in xvnc_argv
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


def test_idle_timeout_auto_stops():
    m = _mgr()
    m.start(email="a@x.com", session_key="sk", idle_timeout_s=0.05)
    for _ in range(50):
        if not m.status()["running"]:
            break
        threading.Event().wait(0.02)
    assert m.status()["running"] is False


def test_wait_x_socket_times_out_fast(tmp_path):
    from server.takeover_browser import wait_x_socket
    import pytest
    with pytest.raises(TimeoutError):
        wait_x_socket(":100", timeout=0.2, poll=0.02, sock_dir=str(tmp_path))


def test_wait_x_socket_succeeds_when_present(tmp_path):
    from server.takeover_browser import wait_x_socket
    (tmp_path / "X100").write_text("")  # 伪造 X socket 文件
    wait_x_socket(":100", timeout=0.5, poll=0.02, sock_dir=str(tmp_path))  # 不抛即通过
