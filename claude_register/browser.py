"""Playwright 操作。不依赖 anymail —— 验证码作为参数传入。"""

from __future__ import annotations

import contextvars
import os
from contextlib import contextmanager
from pathlib import Path

from camoufox.sync_api import Camoufox
from playwright.sync_api import Page, expect

from claude_register.console import log, prompt

URL = "https://claude.ai/login"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"

_output_dir: contextvars.ContextVar = contextvars.ContextVar("output_dir", default=OUTPUT_DIR)


def set_output_dir(path) -> contextvars.Token:
    return _output_dir.set(Path(path))


def screenshot(page: Page, name: str) -> Path:
    d = _output_dir.get()
    d.mkdir(parents=True, exist_ok=True)
    path = d / name
    page.screenshot(path=path, full_page=True)
    log(f"截图已保存：{path}")
    return path


@contextmanager
def browser_session():
    """启动 Camoufox（Firefox 系隐身浏览器）会话。

    headless="virtual" 自动包 Xvfb，适配无显示的容器，且比真 headless 更抗
    Cloudflare 检测；humanize 提供人性化光标移动；locale/geoip 让指纹统一。
    """
    cm = Camoufox(
        headless="virtual",
        humanize=True,
        locale="en-US",
        geoip=True,
        window=(1280, 900),
    )
    # 真正的启动发生在 __enter__（拉起 Firefox / Xvfb），构造函数不会抛——所以只包
    # __enter__ 才能拦到「没 fetch 二进制」「缺 Xvfb」这类启动失败，并给出可操作的提示。
    # 不能用 `with` 把 yield 也裹进 try，否则调用方 body 里的页面异常会被误报成启动失败。
    try:
        browser = cm.__enter__()
    except Exception as exc:
        raise RuntimeError(
            f"启动 Camoufox 失败（{exc}）。请先运行 `uv run camoufox fetch` "
            "下载浏览器二进制，并确认已安装 Xvfb。"
        ) from exc
    log("已启动 Camoufox（headless=virtual）")
    try:
        yield browser
    finally:
        cm.__exit__(None, None, None)


def new_page(browser):
    context = browser.new_context(
        no_viewport=True,
    )
    page = context.new_page()
    page.set_default_timeout(30_000)
    return context, page


def open_login(page: Page) -> None:
    log(f"正在打开：{URL}")
    page.goto(URL, wait_until="domcontentloaded", timeout=60_000)
    log(f"页面标题：{page.title()}")
    log(f"当前地址：{page.url}")


def open_magic_link(page: Page, link: str) -> bool:
    """打开魔术登录链接完成登录。返回 False 表示打开失败。"""
    try:
        page.goto(link, wait_until="domcontentloaded", timeout=60_000)
        log("已打开登录链接。")
    except Exception as exc:
        log(f"打开登录链接失败（{exc}）。")
        return False
    return True


def wait_login_form(page: Page, timeout_ms: int = 120_000) -> None:
    """等邮箱输入框出现；Cloudflare 验证期间轮询并打印状态。"""
    email_box = page.get_by_placeholder("Enter your email")
    step = 3_000
    waited = 0
    while waited < timeout_ms:
        try:
            if email_box.is_visible():
                log("登录表单已出现。")
                return
        except Exception:
            pass
        try:
            title = page.title()
        except Exception:
            title = "(无法读取标题)"
        log(f"等待登录表单… {waited // 1000}s 标题={title!r} url={page.url}")
        page.wait_for_timeout(step)
        waited += step
    shot = screenshot(page, "waiting_login.png")
    raise RuntimeError(f"登录表单未出现（可能卡在 Cloudflare 验证页）。已截图：{shot}")


def fill_email(page: Page, email: str) -> None:
    email_box = page.get_by_placeholder("Enter your email")
    expect(email_box).to_be_visible(timeout=10_000)
    email_box.click()
    email_box.fill("")
    email_box.press_sequentially(email, delay=30)
    log(f"已填入邮箱：{email}")

    continue_btn = page.get_by_role("button", name="Continue with email")
    expect(continue_btn).to_be_enabled(timeout=10_000)
    continue_btn.click()
    log("已点击 Continue with email")


def pause_for_user() -> None:
    """浏览器保持打开，等用户看完。CLAUDE_REGISTER_NO_PAUSE=1 可跳过。"""
    if os.getenv("CLAUDE_REGISTER_NO_PAUSE", "").strip().lower() in {"1", "true", "yes"}:
        log("CLAUDE_REGISTER_NO_PAUSE=1，跳过手动暂停。")
        return
    prompt("浏览器保持打开。看完后在终端按回车关闭…")


def _code_input(page: Page):
    """定位验证码输入框。Task 6 实测：单个 input，data-testid="code" 最稳。

    找不到返回 None（调用方降级，不抛异常）。
    """
    try:
        box = page.get_by_test_id("code")
        if box.count() >= 1 and box.first.is_visible():
            return box.first
    except Exception:
        pass
    # 兜底：data-testid 若改版，用同一元素的另外两个稳定属性
    for build in (
        lambda: page.locator("input[autocomplete='one-time-code']"),
        lambda: page.get_by_label("Login code"),
    ):
        try:
            loc = build()
            if loc.count() >= 1 and loc.first.is_visible():
                return loc.first
        except Exception:
            continue
    return None


def _reveal_code_input(page: Page) -> bool:
    """点掉中间态的「Enter verification code」按钮，把验证码输入框展开出来。

    Task 6 实测：fill_email 之后先落在一个 0 个 input 的提示界面，
    必须点这个按钮才会出现输入框。已经在验证码界面时直接返回 True。
    """
    if _code_input(page) is not None:
        return True
    try:
        btn = page.get_by_test_id("enter-code")
        if btn.count() >= 1 and btn.first.is_visible():
            btn.first.click()
            log("已点击 Enter verification code")
            return True
    except Exception as exc:
        log(f"点击 Enter verification code 失败（{exc}）。")
    return False


def wait_code_screen(page: Page, timeout_ms: int = 60_000) -> bool:
    """等验证码输入框出现，中途自动点掉中间态按钮。

    返回 False 表示没等到（调用方降级，不抛异常）。
    """
    step = 2_000
    waited = 0
    while waited < timeout_ms:
        if _code_input(page) is not None:
            log("验证码界面已出现。")
            return True
        _reveal_code_input(page)
        if _code_input(page) is not None:
            log("验证码界面已出现。")
            return True
        try:
            current_url = page.url
        except Exception as exc:
            log(f"页面或上下文不可用（{exc}），停止等待。")
            return False
        log(f"等待验证码界面… {waited // 1000}s url={current_url}")
        try:
            page.wait_for_timeout(step)
        except Exception as exc:
            log(f"等待期间页面或上下文失效（{exc}），停止等待。")
            return False
        waited += step
    log("验证码界面未在超时内出现。")
    return False


def hcaptcha_visible(page: Page) -> bool:
    """检测提交后是否弹了 hCaptcha 拖拽验证。

    Task 6 实测：点提交会触发 api.hcaptcha.com/getcaptcha，并弹出拖拽题。
    这里只负责如实告知调用方，不尝试自动绕过。
    """
    # 注意：以下选择器是根据实测的请求 URL（api.hcaptcha.com/getcaptcha/...、a-cdn.claude.ai/fc/gt2/public_key/...）
    # 和 hCaptcha 惯例推断出来的，但后提交弹窗的 HTML 未曾被捕获 dump，因此不是从 DOM 实测得出。
    # Task 8 用真实验证码时应重点关注这些选择器是否准确。
    for sel in (
        "iframe[src*='hcaptcha.com']",
        "iframe[title*='hCaptcha']",
        "iframe[src*='/fc/gt2/']",
    ):
        try:
            loc = page.locator(sel)
            if loc.count() >= 1 and loc.first.is_visible():
                return True
        except Exception:
            continue
    return False


def fill_code(page: Page, code: str) -> bool:
    """填验证码并提交。返回 False 表示填不进去（调用方打印验证码让人手填）。"""
    box = _code_input(page)
    if box is None:
        log("找不到验证码输入框。")
        return False

    try:
        box.click()
        box.fill("")
        box.press_sequentially(code, delay=50)
        log(f"已填入验证码：{code}")
    except Exception as exc:
        log(f"填验证码失败（{exc}）。")
        return False

    return _submit_code(page)


def _submit_code(page: Page) -> bool:
    """点提交按钮。Task 6 实测：data-testid="continue"，不会自动提交。

    注意：提交按钮的 disabled 恒为 False，不能用它判断输入是否填满。
    """
    try:
        btn = page.get_by_test_id("continue")
        if btn.count() >= 1 and btn.first.is_visible():
            btn.first.click()
            log("已点击提交（Verify Email Address）")
            return True
    except Exception as exc:
        log(f"点击提交按钮失败（{exc}）。")
        return False
    log("未找到提交按钮。")
    return False
