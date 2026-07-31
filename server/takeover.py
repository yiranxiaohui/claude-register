"""接管会话：注入 sessionKey 的已登录 claude.ai 浏览器 + KasmVNC，供网页接管。

与注册流程（server/runner.py）平级、各用各的屏：注册走 Camoufox 的 "virtual"
自选屏，接管用 KasmVNC 的 Xvnc 直接当 :100 的 X 服务器（自带 Web 客户端与
websocket 推流，比 Xvfb+x11vnc+noVNC 三件套流畅得多，进程也从两个减到一个）。
单例：同一时刻只允许一个接管会话。Xvnc 只绑 localhost，不对外暴露端口，
由面板反代（server/app.py 的 /vnc/*）复用密码鉴权。
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


# KasmVNC 的 Web 资源目录（deb 安装的固定路径），Xvnc 的 -httpd 指到这里。
KASM_WWW = "/usr/share/kasmvnc/www"


class TakeoverManager:
    def __init__(self, *, now_fn, launcher=None, browser_fn=None,
                 wait_display_fn=None, display=":100", web_port=6901):
        self.now_fn = now_fn
        self.launcher = launcher or ProcessLauncher()
        self._browser_fn = browser_fn
        if wait_display_fn is not None:
            self._wait_display = wait_display_fn
        else:
            from server.takeover_browser import wait_x_socket
            self._wait_display = lambda display: wait_x_socket(display)
        self.display = display
        self.web_port = web_port
        self._lock = threading.RLock()
        self._active = False
        self._email = None
        self._started_at = None
        self._xvnc = None
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
                # KasmVNC 的 Xvnc 本身就是 X 服务器，浏览器直接挂上去，无需
                # Xvfb+x11vnc 两级。要点：
                # - 只监听 localhost 的 websocket 口（实测无遗留 RFB/X TCP 口）；
                # - -SecurityTypes None + -DisableBasicAuth：鉴权由面板反代做；
                # - -publicIP 127.0.0.1：跳过启动时的 STUN 公网探测（外网被墙时
                #   会卡住 websocket 监听迟迟不起来）；WebRTC/UDP 用不到。
                self._xvnc = self.launcher.spawn([
                    "Xvnc", self.display, "-geometry", "1280x900", "-depth", "24",
                    "-interface", "127.0.0.1",
                    "-websocketPort", str(self.web_port),
                    "-SecurityTypes", "None", "-DisableBasicAuth",
                    "-sslOnly", "0", "-publicIP", "127.0.0.1",
                    "-httpd", KASM_WWW,
                ])
                self._wait_display(self.display)
                self._browser = self._browser_fn(
                    session_key=session_key, proxy=proxy, display=self.display,
                )
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
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._xvnc is not None:
            _terminate(self._xvnc)
            self._xvnc = None
