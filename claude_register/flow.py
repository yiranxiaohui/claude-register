"""Claude login flow via AnyMail. Entry: main.py"""

from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

from claude_register.anymail import AnyMailClient, Mailbox, load_dotenv

URL = "https://claude.ai/login"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"


def log(msg: str) -> None:
    print(msg, flush=True)


def open_login(page: Page) -> None:
    log(f"正在打开：{URL}")
    page.goto(URL, wait_until="domcontentloaded", timeout=60_000)
    log(f"页面标题：{page.title()}")
    log(f"当前地址：{page.url}")


def wait_login_form(page: Page, timeout_ms: int = 120_000) -> None:
    """等待邮箱输入框出现；Cloudflare 验证期间会轮询并打印状态。"""
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
        title = ""
        try:
            title = page.title()
        except Exception:
            title = "(无法读取标题)"
        log(f"等待登录表单… {waited // 1000}s 标题={title!r} url={page.url}")
        page.wait_for_timeout(step)
        waited += step
    shot = OUTPUT_DIR / "waiting_login.png"
    page.screenshot(path=shot)
    raise RuntimeError(
        f"登录表单未出现（可能卡在 Cloudflare 验证页）。已截图：{shot}"
    )


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


def _prompt(msg: str) -> str:
    try:
        return input(msg).strip()
    except EOFError:
        return ""


def choose_domain(client: AnyMailClient, preferred: str | None = None) -> str:
    if preferred:
        return preferred.strip().lstrip("@").strip(".").lower()

    if client.domain:
        return client.domain

    domains = client.list_domains()
    if not domains:
        raise ValueError(
            "没有可用域名。请在 .env 设置 ANYMAIL_DOMAIN，或给 API Key 加上 domains:read。"
        )
    if len(domains) == 1:
        log(f"使用唯一域名：{domains[0]}")
        return domains[0]

    log("可用域名：")
    for i, dom in enumerate(domains, start=1):
        log(f"  [{i}] {dom}")
    while True:
        raw = _prompt("请选择域名编号（直接回车=1）：")
        if not raw:
            return domains[0]
        if raw.isdigit() and 1 <= int(raw) <= len(domains):
            return domains[int(raw) - 1]
        log("输入无效，请重试。")


def create_custom_mailbox(client: AnyMailClient, domain: str | None = None) -> Mailbox:
    dom = choose_domain(client, domain)
    while True:
        local = _prompt(f"请输入邮箱前缀（将使用 @{dom}）：").strip().lower()
        if not local:
            log("前缀不能为空。")
            continue
        if "@" in local:
            log("只需输入 @ 前面的部分。")
            continue
        email = f"{local}@{dom}"
        log(f"将使用：{email}")
        return client.get_or_create_mailbox(email)


def choose_mailbox(
    client: AnyMailClient,
    *,
    email: str | None = None,
    domain: str | None = None,
    create_new: bool = False,
) -> Mailbox:
    """选择邮箱：命令行指定 / 列表选择 / 自定义新建。"""
    if email:
        box = client.get_or_create_mailbox(email)
        log(f"使用指定邮箱：{box.email} (id={box.id or 'new'})")
        return box

    if create_new:
        return create_custom_mailbox(client, domain)

    accounts: list[Mailbox] = []
    try:
        accounts = client.list_accounts(limit=100)
    except Exception as exc:
        log(f"无法列出已有邮箱（{exc}），改为新建。")
        return create_custom_mailbox(client, domain)

    if not accounts:
        log("AnyMail 中暂无已有邮箱，改为新建。")
        return create_custom_mailbox(client, domain)

    log("请选择邮箱：")
    for i, box in enumerate(accounts, start=1):
        extra = []
        if box.tag:
            extra.append(f"tag={box.tag}")
        if box.expires_at:
            extra.append(f"exp={box.expires_at}")
        suffix = f" ({', '.join(extra)})" if extra else ""
        log(f"  [{i}] {box.email}{suffix}")
    log("  [N] 新建自定义邮箱")
    log("  [直接输入完整邮箱地址]")

    while True:
        raw = _prompt("请输入编号 / N / 邮箱：")
        if not raw:
            log("请输入有效选项。")
            continue
        if raw.lower() in {"n", "new"}:
            return create_custom_mailbox(client, domain)
        if raw.isdigit() and 1 <= int(raw) <= len(accounts):
            box = accounts[int(raw) - 1]
            log(f"已选择：{box.email}")
            return box
        if "@" in raw:
            box = client.get_or_create_mailbox(raw)
            log(f"已选择/创建：{box.email}")
            return box
        log("输入无效，请重试。")


def run_browser(email: str) -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = launch_browser(p)
        context = browser.new_context(
            locale="en-US",
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()
        page.set_default_timeout(30_000)

        open_login(page)
        wait_login_form(page)
        fill_email(page, email)

        page.wait_for_timeout(2_000)
        shot = OUTPUT_DIR / "email_filled.png"
        page.screenshot(path=shot, full_page=True)
        log(f"截图已保存：{shot}")
        log(f"当前地址：{page.url}")
        log(f"页面标题：{page.title()}")

        if os.getenv("CLAUDE_REGISTER_NO_PAUSE", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }:
            log("CLAUDE_REGISTER_NO_PAUSE=1，跳过手动暂停。")
        else:
            input("浏览器保持打开。看完后在终端按回车关闭…")

        context.close()
        browser.close()


def run(
    *,
    email: str | None = None,
    domain: str | None = None,
    create_new: bool = False,
) -> None:
    load_dotenv()
    client = AnyMailClient(domain=domain)
    mailbox = choose_mailbox(
        client,
        email=email,
        domain=domain,
        create_new=create_new,
    )
    log(f"最终邮箱：{mailbox.email} (id={mailbox.id})")
    run_browser(mailbox.email)
    log("完成。")
    log(f"本次邮箱：{mailbox.email}")
    if mailbox.id:
        log(f"邮箱 id：{mailbox.id}")
