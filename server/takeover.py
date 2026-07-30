"""接管会话：注入 sessionKey 的已登录 claude.ai 浏览器 + x11vnc，供 noVNC 接管。

与注册流程（server/runner.py）平级、各用各的屏：注册走 Camoufox 的 "virtual"
自选屏，接管自己管一块固定的 Xvfb :100，x11vnc 精确挂上去。单例：同一时刻
只允许一个接管会话。全部子进程只绑 localhost，不对外暴露端口。
"""
from __future__ import annotations

import subprocess
import threading

from claude_register import console


class TakeoverBusy(Exception):
    pass


class TakeoverError(Exception):
    pass


class ProcessLauncher:
    """subprocess.Popen 的薄封装，便于测试注入假实现。"""

    def spawn(self, argv: list[str]):
        return subprocess.Popen(argv)


def _terminate(proc) -> None:
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


class TakeoverManager:
    def __init__(self, *, now_fn, launcher=None, browser_fn=None,
                 wait_display_fn=None, display=":100", vnc_port=5900):
        self.now_fn = now_fn
        self.launcher = launcher or ProcessLauncher()
        self._browser_fn = browser_fn
        if wait_display_fn is not None:
            self._wait_display = wait_display_fn
        else:
            from server.takeover_browser import wait_x_socket
            self._wait_display = lambda display: wait_x_socket(display)
        self.display = display
        self.vnc_port = vnc_port
        self._lock = threading.RLock()
        self._active = False
        self._email = None
        self._started_at = None
        self._xvfb = None
        self._x11vnc = None
        self._browser = None
        self._timer = None

    def status(self) -> dict:
        with self._lock:
            return {
                "running": self._active,
                "email": self._email,
                "started_at": self._started_at,
            }

    def start(self, *, email, session_key, proxy="", idle_timeout_s=900) -> dict:
        with self._lock:
            if self._active:
                raise TakeoverBusy("已有接管会话，请先结束")
            if self._browser_fn is None:
                from server.takeover_browser import open_takeover_browser
                self._browser_fn = open_takeover_browser
            try:
                self._xvfb = self.launcher.spawn([
                    "Xvfb", self.display, "-screen", "0", "1280x900x24",
                    "-nolisten", "tcp",
                ])
                self._wait_display(self.display)
                self._browser = self._browser_fn(
                    session_key=session_key, proxy=proxy, display=self.display,
                )
                self._x11vnc = self.launcher.spawn([
                    "x11vnc", "-display", self.display, "-localhost", "-forever",
                    "-shared", "-rfbport", str(self.vnc_port), "-nopw", "-quiet",
                ])
            except Exception as exc:  # noqa: BLE001
                self._teardown()
                raise TakeoverError(f"启动接管会话失败：{exc}") from exc
            self._active = True
            self._email = email
            self._started_at = self.now_fn()
            console.log(f"接管会话已启动：{email}")
            self._timer = threading.Timer(idle_timeout_s, self._idle_stop)
            self._timer.daemon = True
            self._timer.start()
            return {"email": email, "started_at": self._started_at}

    def _idle_stop(self):
        console.log("接管会话空闲超时，自动结束。")
        self.stop()

    def stop(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._teardown()
            self._active = False
            self._email = None
            self._started_at = None

    def _teardown(self):
        if self._x11vnc is not None:
            _terminate(self._x11vnc)
            self._x11vnc = None
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._xvfb is not None:
            _terminate(self._xvfb)
            self._xvfb = None
