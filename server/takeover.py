"""接管会话：注入 sessionKey 的已登录 claude.ai 浏览器 + Xpra，供网页接管。

与注册流程（server/runner.py）平级、各用各的屏：注册走 Camoufox 的 "virtual"
自选屏，接管用 Xpra start-desktop 管理独立的 :100 虚拟桌面，并直接提供 HTML5
客户端和 WebSocket 传输。Xpra 客户端支持自动重连和双向系统剪贴板同步。
单例：同一时刻只允许一个接管会话。Xpra 只绑 localhost，不对外暴露端口，
由同容器 Nginx 反代，并通过 FastAPI auth_request 复用面板鉴权。
"""
from __future__ import annotations

import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor

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
                 wait_display_fn=None, wait_web_fn=None,
                 display=":100", web_port=14500):
        self.now_fn = now_fn
        self.launcher = launcher or ProcessLauncher()
        self._browser_fn = browser_fn
        if wait_display_fn is not None:
            self._wait_display = wait_display_fn
        else:
            from server.takeover_browser import wait_x_socket
            self._wait_display = lambda display: wait_x_socket(display)
        if wait_web_fn is not None:
            self._wait_web = wait_web_fn
        else:
            from server.takeover_browser import wait_tcp_port
            self._wait_web = wait_tcp_port
        self.display = display
        self.web_port = web_port
        self._lock = threading.RLock()
        self._active = False
        self._email = None
        self._started_at = None
        self._xpra = None
        self._browser = None
        self._browser_executor = None
        self._timer = None
        self._timer_generation = 0
        self._idle_timeout_s = 0.0

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
                # Xpra 自己拉起虚拟 X 桌面并提供 HTML5/WebSocket：
                # - TCP 只绑 localhost，外层 Nginx + 面板 Cookie 负责鉴权；
                # - 关闭 RFB 升级，避免意外提供传统 VNC 协议；
                # - 双向剪贴板和 ping 保活显式开启，断线重连由 HTML5 客户端负责；
                # - 音频、打印、传文件等接管不需要的子系统全部关闭，减少进程和故障面。
                self._xpra = self.launcher.spawn([
                    "xpra", "start-desktop", self.display,
                    "--daemon=no", "--start-via-proxy=no", "--systemd-run=no",
                    # 浏览器由本进程（而不是 Xpra --start-child）创建，因此虚拟屏
                    # 关闭 Xauthority 校验；-nolisten tcp 保证该 X server 仍只在容器
                    # 本地 UNIX socket 可达。
                    "--xvfb=Xvfb +extension GLX +extension Composite "
                    "-screen 0 8192x4096x24+32 -nolisten tcp -noreset -ac -dpi 96x96",
                    "--bind=none",
                    f"--bind-tcp=127.0.0.1:{self.web_port},auth=none",
                    "--html=on", "--ssl=off", "--rfb-upgrade=0",
                    "--http-scripts=off", "--mdns=no",
                    "--resize-display=1280x900", "--sharing=yes",
                    "--clipboard=yes", "--clipboard-direction=both", "--pings=5",
                    "--speaker=off", "--microphone=off", "--pulseaudio=no",
                    "--printing=no", "--file-transfer=no", "--webcam=no",
                    "--notifications=no", "--tray=no", "--system-tray=no",
                    "--dbus-launch=no", "--dbus-control=no", "--dbus-proxy=no",
                    "--start-new-commands=no", "--exit-with-children=no",
                ])
                self._wait_display(self.display)
                self._wait_web("127.0.0.1", self.web_port)
                # Playwright 的 Sync API 通过线程绑定的 greenlet 驱动事件循环。
                # FastAPI 的启动、停止请求可能落到不同工作线程，因此浏览器的
                # 创建和关闭都必须显式派发到同一条专用线程。
                self._browser_executor = ThreadPoolExecutor(
                    max_workers=1, thread_name_prefix="takeover-browser",
                )
                self._browser = self._browser_executor.submit(
                    self._browser_fn,
                    session_key=session_key,
                    proxy=proxy,
                    display=self.display,
                ).result()
            except Exception as exc:  # noqa: BLE001
                console.log(f"启动接管会话失败：{exc}")
                self._teardown()
                raise TakeoverError(f"启动接管会话失败：{exc}") from exc
            self._active = True
            self._email = email
            self._started_at = self.now_fn()
            console.log(f"接管会话已启动：{email}")
            self._idle_timeout_s = float(idle_timeout_s)
            self._arm_idle_timer_locked()
            return {"email": email, "started_at": self._started_at}

    def _arm_idle_timer_locked(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
        self._timer_generation += 1
        generation = self._timer_generation
        self._timer = threading.Timer(
            self._idle_timeout_s, self._idle_stop, args=(generation,),
        )
        self._timer.daemon = True
        self._timer.start()

    def touch(self) -> None:
        """续期活动接管会话的空闲回收计时器。"""
        with self._lock:
            if not self._active:
                raise TakeoverError("当前没有活动的接管会话")
            self._arm_idle_timer_locked()

    def relogin(self, **kwargs) -> str:
        """在活动接管浏览器所属线程执行一次自动重新登录。"""
        with self._lock:
            if not self._active or self._browser is None or self._browser_executor is None:
                raise TakeoverError("当前没有活动的接管会话")
            relogin = getattr(self._browser, "relogin", None)
            if not callable(relogin):
                raise TakeoverError("当前接管浏览器不支持重新登录")
            try:
                return self._browser_executor.submit(relogin, **kwargs).result()
            except TakeoverError:
                raise
            except Exception as exc:  # noqa: BLE001
                console.log(f"接管浏览器重新登录失败：{exc}")
                raise TakeoverError(str(exc)) from exc

    def _idle_stop(self, generation: int):
        with self._lock:
            # cancel() 与回调启动可能竞争；旧一代计时器不得关闭
            # 已经被心跳续期的会话。
            if not self._active or generation != self._timer_generation:
                return
            console.log("接管会话空闲超时，自动结束。")
            self.stop()

    def stop(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._timer_generation += 1
            self._teardown()
            self._active = False
            self._email = None
            self._started_at = None
            self._idle_timeout_s = 0.0

    def _teardown(self):
        browser = self._browser
        browser_executor = self._browser_executor
        self._browser = None
        self._browser_executor = None
        if browser is not None:
            try:
                if browser_executor is not None:
                    browser_executor.submit(browser.close).result()
                else:
                    browser.close()
            except Exception as exc:  # noqa: BLE001
                console.log(f"关闭接管浏览器失败：{exc}")
        if browser_executor is not None:
            browser_executor.shutdown(wait=True, cancel_futures=True)
        if self._xpra is not None:
            _terminate(self._xpra)
            self._xpra = None
