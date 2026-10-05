"""注册流程用的 Xvfb 虚拟显示。

Chromium 没有 Camoufox 那种自带的 "virtual" 档：无显示器的 Linux 上要么真 headless
（指纹弱、更容易被 Cloudflare 拦），要么自己拉一个 Xvfb 再以有头模式挂上去。这里做后者。

显示号从 DISPLAY_RANGE 里挑一个没被占用的：:100 留给接管桌面（server/takeover.py），
不能用 Xvfb 的 -displayfd 自动选号——它从 :0 往上找，接管没开时会抢走 :100。
"""
from __future__ import annotations

import os
import subprocess
import time

# 注册专用的显示号区间，避开接管桌面的 :100。
DISPLAY_RANGE = range(110, 200)
SCREEN_SIZE = (1920, 1080)
X11_DIR = "/tmp/.X11-unix"


def display_in_use(num: int, *, sock_dir: str = X11_DIR, lock_dir: str = "/tmp") -> bool:
    return (
        os.path.exists(os.path.join(sock_dir, f"X{num}"))
        or os.path.exists(os.path.join(lock_dir, f".X{num}-lock"))
    )


class VirtualDisplay:
    """拉起一个 Xvfb，`name` 是 ":<num>"，用完 stop()。"""

    def __init__(self, *, size: tuple[int, int] = SCREEN_SIZE, launcher=None,
                 in_use=display_in_use, sock_dir: str = X11_DIR,
                 timeout: float = 10.0, poll: float = 0.05):
        self.size = size
        self._launch = launcher or (lambda argv: subprocess.Popen(
            argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ))
        self._in_use = in_use
        self._sock_dir = sock_dir
        self._timeout = timeout
        self._poll = poll
        self._proc = None
        self.name: str | None = None

    def start(self) -> VirtualDisplay:
        width, height = self.size
        for num in DISPLAY_RANGE:
            if self._in_use(num):
                continue
            proc = self._launch([
                "Xvfb", f":{num}", "-screen", "0", f"{width}x{height}x24",
                "-nolisten", "tcp", "-noreset",
            ])
            if self._wait_ready(proc, num):
                self._proc = proc
                self.name = f":{num}"
                return self
            # 进程起不来通常是同号被并发抢占（锁文件检查与启动之间的竞争），换下一个号。
            _terminate(proc)
        raise RuntimeError("找不到可用的 X 显示号，Xvfb 启动失败")

    def _wait_ready(self, proc, num: int) -> bool:
        sock = os.path.join(self._sock_dir, f"X{num}")
        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return False
            if os.path.exists(sock):
                return True
            time.sleep(self._poll)
        return False

    def stop(self) -> None:
        if self._proc is not None:
            _terminate(self._proc)
            self._proc = None
        self.name = None


def _terminate(proc) -> None:
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
