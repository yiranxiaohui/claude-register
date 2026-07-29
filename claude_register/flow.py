"""编排：选后缀 → 建邮箱 → 填邮箱 → 优先等魔术登录链接（拿不到再退回接码）→ 打开/填入。入口 main.py"""

from __future__ import annotations

from claude_register.anymail import AnyMailClient, Mailbox, load_dotenv
from claude_register.browser import (
    browser_session,
    fill_code,
    fill_email,
    hcaptcha_visible,
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
from server.config_store import Config

# 验证码兜底轮询最多占用总预算（--login-timeout）的这一比例，且不超过下面的上限秒数——
# 两者取较小值，剩下的预算都留给魔术链接轮询，这样 --login-timeout 才是「总等待时长」的
# 真实上限，而不是「魔术链接超时 + 额外 30 秒」。
FALLBACK_CODE_TIMEOUT_FRACTION = 0.25
FALLBACK_CODE_TIMEOUT_CAP = 30.0


def _split_login_timeout(total: float) -> tuple[float, float]:
    """把总等待预算拆成 (魔术链接超时, 验证码兜底超时)，两者之和不超过 total。"""
    fallback = min(FALLBACK_CODE_TIMEOUT_CAP, total * FALLBACK_CODE_TIMEOUT_FRACTION)
    link = max(0.0, total - fallback)
    return link, fallback


def _report_manual_fallback(mailbox: Mailbox, client: AnyMailClient) -> None:
    """降级时必须让用户拿到继续操作所需的一切。

    Claude 发的几乎都是登录链接，不是验证码——即便脚本这边判定超时，
    链接也可能是最后一刻才到，去后台先找登录链接，验证码只是次要可能。
    """
    banner(f"邮箱：{mailbox.email}")
    log(f"AnyMail 后台：{client.base_url}")
    log("可以去后台查收登录链接（Secure link to log in），复制到浏览器打开；"
        "如果收到的是验证码，也可以在浏览器里手动填入。")


def run_browser(
    client: AnyMailClient,
    mailbox: Mailbox,
    since: str,
    *,
    auto_login: bool,
    code_timeout: float,
) -> None:
    with browser_session() as browser:
        context, page = new_page(browser)
        try:
            open_login(page)
            wait_login_form(page)
            fill_email(page, mailbox.email)

            screen_ok = wait_code_screen(page)
            if not screen_ok:
                # wait_code_screen 返回 False 有两种可能：真超时（页面还活着）或者
                # 页面/上下文已经死掉；后一种情况下这个 screenshot 会直接抛
                # TargetClosedError。绝不能让它抢在 poll_magic_link 之前把整个
                # run_browser 打断——接邮件才是这里真正有价值的部分，一次截图失败
                # 不该浪费掉已经建好的邮箱。
                try:
                    screenshot(page, "code_screen_missing.png")
                except Exception as exc:
                    log(f"截图失败（{exc}），忽略，继续等待邮件。")
                log("验证码界面未出现，但仍继续等待——魔术链接/验证码本身有价值。")

            link_timeout, fallback_timeout = _split_login_timeout(code_timeout)
            link: str | None = None
            code: str | None = None
            if code_timeout > 0:
                link = client.poll_magic_link(
                    to=mailbox.email, since=since, timeout=link_timeout
                )
                if link is None:
                    # 退一步试试 6 位码——Claude 的 UI 里存在这条路径。超时从
                    # --login-timeout 的总预算里扣除，不能在它之外再固定多等一段。
                    log(f"未收到登录链接，改试 6 位验证码（最多 {fallback_timeout:.0f}s）。")
                    code = client.poll_code(
                        to=mailbox.email, since=since, timeout=fallback_timeout
                    )
            else:
                log("--login-timeout 0，跳过等待邮件。")

            try:
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
            except Exception as exc:
                # 到这一步，链接/验证码已经从邮箱里取出来了——而且不能重新获取
                # （魔术链接一次性；邮箱不会再收到新邮件）。哪怕这里的收尾动作
                # （截图、读 URL 之类）出错，也绝不能让异常直接冲到 finally 把浏览器
                # 关掉，那样等于凭空浪费一个可能已经登录成功、无法再拿一次的凭证。
                log(f"登录后续操作出错（{exc}），但凭证已经取出且无法重新获取，"
                    "浏览器会保持打开，请手动检查当前页面状态。")

            pause_for_user()
        finally:
            context.close()
            # browser 的关闭交给 browser_session 的上下文退出，这里不重复关。


def run(
    *,
    email: str | None = None,
    domain: str | None = None,
    auto_login: bool = True,
    code_timeout: float = 120.0,
    config: Config | None = None,
) -> None:
    load_dotenv()
    if email and domain:
        log("已指定 --email，忽略 --domain（邮箱已含后缀）。")

    if config is not None:
        client = AnyMailClient(
            base_url=config.anymail_base_url or None,
            api_key=config.anymail_api_key or None,
            domain=config.anymail_domain or domain,
            code_regex=config.register_code_regex or None,
        )
        expires_hours = (
            None if config.anymail_expires_hours <= 0 else config.anymail_expires_hours
        )
        auto_login = config.register_auto_login
        code_timeout = config.register_login_timeout
    else:
        client = AnyMailClient(domain=domain)
        expires_hours = None

    mailbox, since = prepare_mailbox(
        client, email=email, domain=domain, expires_hours=expires_hours
    )
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
