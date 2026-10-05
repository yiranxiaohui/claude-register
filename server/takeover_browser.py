"""接管会话的真实副作用实现：等 X socket 就绪、开注入 Cookie 的 Chromium。

与 server/takeover.py 分开，好让 TakeoverManager 单测完全不碰浏览器 / 文件系统。
"""
from __future__ import annotations

import os
import socket
import time

from claude_register.anymail import AnyMailClient
from claude_register.browser import (
    BROWSER_INSTALL_HINT,
    TAKEOVER_DENY_HOSTS,
    URL as LOGIN_URL,
    build_proxy_options,
    extract_session_key,
    fill_code,
    fill_email,
    hcaptcha_visible,
    launch_chromium,
    new_context,
    open_login,
    open_magic_link,
    wait_code_screen,
    wait_for_session_key,
    wait_login_form,
)
from claude_register.console import log
from claude_register.flow import _split_login_timeout
from claude_register.mailbox import utc_now_iso


# 接管中继的上游并发上限。注册流程用默认 3（匹配机场硬限额、连接短命周转快），
# 但接管是交互式浏览：claude.ai 的 HTTP/2 复用连接、SSE 事件流是几分钟不关的
# 长命连接，槽位按隧道生命周期持有，3 个槽会被前三条长命连接钉死，其余请求
# 全在本地排队 30s 然后超时（表现为满屏「等待上游并发槽位超时」）。
DEFAULT_TAKEOVER_MAX_UPSTREAM = 16


def takeover_max_upstream() -> int:
    raw = os.environ.get("TAKEOVER_MAX_UPSTREAM", "")
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_TAKEOVER_MAX_UPSTREAM
    return value if value > 0 else DEFAULT_TAKEOVER_MAX_UPSTREAM


def wait_x_socket(display: str, timeout: float = 10.0, poll: float = 0.1,
                  sock_dir: str = "/tmp/.X11-unix") -> None:
    """轮询等待虚拟 X 服务器的 UNIX socket 出现（display ":100" → 文件 X100）。"""
    num = display.lstrip(":").split(".")[0]
    path = os.path.join(sock_dir, f"X{num}")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.exists(path):
            return
        time.sleep(poll)
    raise TimeoutError(f"等待 X 显示 {display} 就绪超时（{path} 未出现）")


def wait_tcp_port(host: str, port: int, timeout: float = 10.0,
                  poll: float = 0.1, connect_fn=None) -> None:
    """等待 Xpra 的 HTML5/WebSocket 监听口就绪，避免返回一个尚不可用的接管页。"""
    connect = connect_fn or socket.create_connection
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            conn = connect((host, port), timeout=min(poll, 1.0))
        except OSError:
            time.sleep(poll)
            continue
        conn.close()
        return
    raise TimeoutError(f"等待 Xpra Web 端口 {host}:{port} 就绪超时")


class _BrowserHandle:
    def __init__(self, session, relay, page):
        self._session = session
        self._relay = relay
        self._page = page

    def relogin(
        self,
        *,
        email: str,
        mail_base_url: str,
        mail_api_key: str,
        login_timeout: float,
    ) -> str:
        """在当前接管页请求新登录邮件并返回新的 sessionKey。

        该方法由 TakeoverManager 固定派发到创建浏览器的专用线程，不能从
        FastAPI 请求线程直接调用，否则会破坏 Playwright Sync API 的线程绑定。
        """
        page = self._page
        client = AnyMailClient(base_url=mail_base_url, api_key=mail_api_key)
        since = utc_now_iso()

        # 旧 cookie 正是本次重新登录的原因。先删掉，避免后续读取时把它误认成
        # 魔术链接刚换回来的新 sessionKey。
        try:
            page.context.clear_cookies(name="sessionKey")
        except TypeError:
            # 兼容不支持按 name 清理的旧 Playwright 版本。
            page.context.clear_cookies()

        log(f"接管浏览器开始为 {email} 重新自动登录。")
        open_login(page)
        wait_login_form(page)
        fill_email(page, email)

        link_timeout, fallback_timeout = _split_login_timeout(login_timeout)
        link = client.poll_magic_link(
            to=email,
            since=since,
            timeout=link_timeout,
        )
        code = None
        if link is None:
            log(f"未收到登录链接，改试 6 位验证码（最多 {fallback_timeout:.0f}s）。")
            screen_ok = wait_code_screen(page)
            code = client.poll_code(
                to=email,
                since=since,
                timeout=fallback_timeout,
            )

        if link:
            if not open_magic_link(page, link):
                raise RuntimeError("已收到登录链接，但在接管浏览器中打开失败")
        elif code:
            if not screen_ok:
                raise RuntimeError("已收到验证码，但接管浏览器未进入验证码页面")
            if not fill_code(page, code):
                raise RuntimeError("已收到验证码，但自动填写或提交失败")
            page.wait_for_timeout(3_000)
            if hcaptcha_visible(page):
                raise RuntimeError("验证码登录触发 hCaptcha，请在接管画面中手动完成")
        else:
            raise RuntimeError("等待登录邮件超时")

        # 接管只允许已有 sessionKey 的老账号启动，因此重新登录无需再跑注册
        # onboarding；新 cookie 才是是否恢复成功的最终判据。
        session_key = wait_for_session_key(page, timeout_ms=30_000)
        if not session_key:
            raise RuntimeError("未取得新的 sessionKey；账号可能已被封或登录未完成")
        log(f"接管浏览器已为 {email} 重新登录并取得新 sessionKey。")
        return session_key

    def session_key(self) -> str:
        """读取接管浏览器当前的 sessionKey Cookie（手动登录后取 sk 用）。"""
        return extract_session_key(self._page) or ""

    def close(self):
        try:
            self._session.close()
        finally:
            if self._relay is not None:
                self._relay.stop()


def _prefill_login_email(page, email: str) -> None:
    """尽力把邮箱预填进登录框（不点继续），失败只记日志，由用户自己输入。"""
    try:
        box = page.get_by_placeholder("Enter your email")
        box.wait_for(state="visible", timeout=20_000)
        box.fill(email)
        log(f"已在登录页预填邮箱：{email}，请在接管画面继续登录。")
    except Exception as exc:  # noqa: BLE001
        log(f"预填登录邮箱失败（{exc}），请在接管画面手动输入。")


def open_takeover_browser(*, session_key: str, proxy: str = "", display: str = ":100",
                          login_email: str = ""):
    """开一个 claude.ai 的 Chromium（挂在指定 X display 上），返回带 .close() 的句柄。

    有 session_key 时注入 Cookie 打开已登录首页；为空时是「手动登录」模式：
    直接打开登录页（可选预填邮箱），由用户在接管画面里完成登录。
    """
    proxy_options, relay, timezone = build_proxy_options(
        proxy or None, max_upstream=takeover_max_upstream(),
        deny_hosts=TAKEOVER_DENY_HOSTS,
    )
    try:
        session = launch_chromium(
            headless=False,
            proxy=proxy_options,
            display=display,  # 挂到 Xpra 管理的 :100 虚拟桌面
            timezone=timezone,
        )
    except Exception as exc:
        if relay is not None:
            relay.stop()
        raise RuntimeError(
            f"启动接管浏览器失败（{exc}）。{BROWSER_INSTALL_HINT}，并确认 Xvfb 可用。"
        ) from exc
    browser = session.browser
    try:
        context = new_context(browser)
        if not session_key:
            page = context.new_page()
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60_000)
            log("手动登录浏览器已打开 claude.ai 登录页。")
            if login_email:
                _prefill_login_email(page, login_email)
            return _BrowserHandle(session, relay, page)
        context.add_cookies([{
            "name": "sessionKey",
            "value": session_key,
            "domain": ".claude.ai",
            "path": "/",
            "secure": True,
            "httpOnly": True,
        }])
        page = context.new_page()
        page.goto("https://claude.ai", wait_until="domcontentloaded", timeout=60_000)
        log("接管浏览器已注入 sessionKey 并打开 claude.ai。")
    except Exception as exc:
        session.close()
        if relay is not None:
            relay.stop()
        raise RuntimeError(f"注入 sessionKey / 打开 claude.ai 失败（{exc}）。") from exc
    return _BrowserHandle(session, relay, page)
