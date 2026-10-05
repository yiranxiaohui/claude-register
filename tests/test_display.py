"""注册用 Xvfb 虚拟显示：选号避开接管的 :100，失败时换号，用完收掉。"""
from __future__ import annotations

import os
import shutil

import pytest

from claude_register import display as display_mod
from claude_register.display import DISPLAY_RANGE, VirtualDisplay


class FakeProc:
    def __init__(self, alive=True):
        self.alive = alive
        self.terminated = False

    def poll(self):
        return None if self.alive else 1

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


def test_range_avoids_takeover_display():
    assert 100 not in DISPLAY_RANGE


def test_skips_displays_in_use_and_waits_for_socket(tmp_path):
    first = DISPLAY_RANGE.start
    launched = []

    def launcher(argv):
        launched.append(argv)
        (tmp_path / argv[1].replace(":", "X")).touch()  # Xvfb 就绪 = socket 出现
        return FakeProc()

    vd = VirtualDisplay(launcher=launcher, in_use=lambda n: n == first,
                        sock_dir=str(tmp_path)).start()

    assert vd.name == f":{first + 1}"
    assert launched[0][:2] == ["Xvfb", f":{first + 1}"]
    assert "-nolisten" in launched[0] and "tcp" in launched[0]
    vd.stop()
    assert vd.name is None


def test_moves_on_when_xvfb_exits_early(tmp_path):
    """同号被并发抢占时 Xvfb 会立刻退出：换下一个号，而不是一直等到超时。"""
    first = DISPLAY_RANGE.start
    procs = []

    def launcher(argv):
        if argv[1] == f":{first}":
            proc = FakeProc(alive=False)
        else:
            (tmp_path / argv[1].replace(":", "X")).touch()
            proc = FakeProc()
        procs.append(proc)
        return proc

    vd = VirtualDisplay(launcher=launcher, in_use=lambda n: False,
                        sock_dir=str(tmp_path)).start()
    assert vd.name == f":{first + 1}"
    assert procs[0].terminated
    vd.stop()
    assert procs[1].terminated


def test_raises_when_no_display_available(tmp_path):
    with pytest.raises(RuntimeError, match="显示号"):
        VirtualDisplay(launcher=lambda argv: FakeProc(), in_use=lambda n: True,
                       sock_dir=str(tmp_path)).start()


def test_display_in_use_checks_socket_and_lock(tmp_path):
    sock_dir = tmp_path / "x11"
    sock_dir.mkdir()
    assert not display_mod.display_in_use(150, sock_dir=str(sock_dir), lock_dir=str(tmp_path))
    (tmp_path / ".X150-lock").touch()
    assert display_mod.display_in_use(150, sock_dir=str(sock_dir), lock_dir=str(tmp_path))


@pytest.mark.skipif(not shutil.which("Xvfb"), reason="需要 Xvfb")
def test_real_xvfb_starts_and_stops():
    vd = VirtualDisplay().start()
    try:
        num = vd.name.lstrip(":")
        assert os.path.exists(f"/tmp/.X11-unix/X{num}")
    finally:
        vd.stop()
