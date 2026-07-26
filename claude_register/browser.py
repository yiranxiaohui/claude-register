"""Playwright 操作。不依赖 anymail —— 验证码作为参数传入。"""

from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import Page, expect

from claude_register.console import log, prompt

URL = "https://claude.ai/login"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"


def screenshot(page: Page, name: str) -> Path:
    OUTPUT_DIR.mkdir(exist_ok=True)
    path = OUTPUT_DIR / name
    page.screenshot(path=path, full_page=True)
    log(f"截图已保存：{path}")
    return path


def launch_browser(p):
    common = {
        "headless": False,
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    try:
        browser = p.chromium.launch(channel="chrome", **common)
        log("已启动本机 Chrome（channel=chrome）")
        return browser
    except Exception as exc:
        log(f"本机 Chrome 不可用（{exc}），回退到 Playwright Chromium")
        return p.chromium.launch(**common)


def new_page(browser):
    context = browser.new_context(
        locale="en-US",
        viewport={"width": 1280, "height": 900},
    )
    page = context.new_page()
    page.set_default_timeout(30_000)
    return context, page


def open_login(page: Page) -> None:
    log(f"正在打开：{URL}")
    page.goto(URL, wait_until="domcontentloaded", timeout=60_000)
    log(f"页面标题：{page.title()}")
    log(f"当前地址：{page.url}")


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
