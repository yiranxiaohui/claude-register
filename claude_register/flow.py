"""编排：选后缀 → 建邮箱 → 填邮箱 → 接码 → 填码。入口 main.py"""

from __future__ import annotations

from playwright.sync_api import sync_playwright

from claude_register.anymail import AnyMailClient, Mailbox, load_dotenv
from claude_register.browser import (
    fill_code,
    fill_email,
    hcaptcha_visible,
    launch_browser,
    new_page,
    open_login,
    open_magic_link,
    pause_for_user,
    screenshot,
    wait_code_screen,
    wait_login_form,
)
from claude_register.console import banner, log
from claude_register.mailbox import prepare_mailbox


def _report_manual_fallback(mailbox: Mailbox, client: AnyMailClient) -> None:
    """降级时必须让用户拿到继续操作所需的一切。"""
    banner(f"邮箱：{mailbox.email}")
    log(f"AnyMail 后台：{client.base_url}")
    log("可以去后台查收验证码，然后在浏览器里手动填入。")


def run_browser(
    client: AnyMailClient,
    mailbox: Mailbox,
    since: str,
    *,
    auto_login: bool,
    code_timeout: float,
) -> None:
    with sync_playwright() as p:
        browser = launch_browser(p)
        context, page = new_page(browser)
        try:
            open_login(page)
            wait_login_form(page)
            fill_email(page, mailbox.email)

            screen_ok = wait_code_screen(page)
            if not screen_ok:
                screenshot(page, "code_screen_missing.png")
                log("验证码界面未出现，但仍继续等待——魔术链接/验证码本身有价值。")

            link: str | None = None
            code: str | None = None
            if code_timeout > 0:
                link = client.poll_magic_link(
                    to=mailbox.email, since=since, timeout=code_timeout
                )
                if link is None:
                    # 退一步试试 6 位码——Claude 的 UI 里存在这条路径
                    log("未收到登录链接，改试 6 位验证码。")
                    code = client.poll_code(
                        to=mailbox.email, since=since, timeout=30.0
                    )
            else:
                log("--login-timeout 0，跳过等待邮件。")

            if link:
                banner("已收到登录链接")
                log(link)
                if not auto_login:
                    log("--no-auto-login，请自己打开上面的链接。")
                elif open_magic_link(page, link):
                    page.wait_for_timeout(3_000)
                    screenshot(page, "after_magic_link.png")
                    log(f"当前地址：{page.url}")
                else:
                    log("打开链接失败，请手动复制上面的链接到浏览器。")
            elif code:
                banner(f"验证码：{code}")
                if not auto_login:
                    log("--no-auto-login，请手动填入上面的验证码。")
                elif not screen_ok:
                    log("验证码界面未确认出现，请手动填入上面的验证码。")
                elif not fill_code(page, code):
                    log("填码框定位不到，请手动填入上面的验证码。")
                    screenshot(page, "fill_code_failed.png")
                else:
                    page.wait_for_timeout(3_000)
                    screenshot(page, "after_code.png")
                    if hcaptcha_visible(page):
                        screenshot(page, "hcaptcha.png")
                        banner("需要人工拖拽 hCaptcha 验证")
                        log("提交验证码后弹出了 hCaptcha 拖拽题（Task 6 已知现象）。")
                        log("请在浏览器里手动完成拖拽，脚本不会自动绕过。")
                    log(f"当前地址：{page.url}")
            else:
                log("既没收到登录链接，也没收到验证码。")
                screenshot(page, "no_mail.png")
                _report_manual_fallback(mailbox, client)

            pause_for_user()
        finally:
            context.close()
            browser.close()


def run(
    *,
    email: str | None = None,
    domain: str | None = None,
    auto_login: bool = True,
    code_timeout: float = 120.0,
) -> None:
    load_dotenv()
    if email and domain:
        log("已指定 --email，忽略 --domain（邮箱已含后缀）。")

    client = AnyMailClient(domain=domain)
    mailbox, since = prepare_mailbox(client, email=email, domain=domain)
    log(f"本次邮箱：{mailbox.email} (id={mailbox.id or 'new'})")

    run_browser(
        client,
        mailbox,
        since,
        auto_login=auto_login,
        code_timeout=code_timeout,
    )

    log("完成。")
    banner(f"邮箱：{mailbox.email}")
    if mailbox.id:
        log(f"邮箱 id：{mailbox.id}")
    log("提示：邮箱默认 24 小时后被 AnyMail 清理，若要长期收信请调整有效期。")
