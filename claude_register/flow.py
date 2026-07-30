"""编排：选后缀 → 建邮箱 → 填邮箱 → 优先等魔术登录链接（拿不到再退回接码）→ 打开/填入。入口 main.py"""

from __future__ import annotations

from claude_register.anymail import AnyMailClient, Mailbox, load_dotenv
from claude_register.accounts import AccountRecord, save_account_record
from claude_register.browser import (
    _output_dir,
    browser_session,
    default_display_name,
    fill_code,
    fill_email,
    finish_after_auth,
    hcaptcha_visible,
    new_page,
    open_login,
    open_magic_link,
    screenshot,
    validate_proxy,
    wait_code_screen,
    wait_for_session_key,
    wait_login_form,
)
from typing import TYPE_CHECKING

from claude_register.console import banner, log
from claude_register.mailbox import prepare_mailbox

if TYPE_CHECKING:
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
    proxy: str | None = None,
    password: str = "",
    poll_client: AnyMailClient | None = None,
    mail_key: str = "",
) -> dict | None:
    """跑浏览器登录/建号。成功拿到 sessionKey 时返回账号 dict，否则 None。

    poll_client：接码轮询用的客户端（子 key）；None 时回落用 client（父 key）。
    mail_key：随账号导出的子 key 明文；降级时空串，父 key 绝不写进导出。
    """
    poll = poll_client or client
    account: dict | None = None
    display_name = default_display_name(mailbox.email)

    def _capture(page) -> dict | None:
        nonlocal account
        session_key = wait_for_session_key(page, timeout_ms=30_000)
        if not session_key:
            log("未在 cookies 中找到 sessionKey，跳过账号保存。")
            try:
                screenshot(page, "session_key_missing.png")
            except Exception:
                pass
            return None
        record = AccountRecord(
            email=mailbox.email,
            password=password or "",
            sessionKey=session_key,
            proxy=proxy or "",
            display_name=display_name,
            mailbox_id=str(mailbox.id or ""),
            mail_key=mail_key,
            mail_base_url=client.base_url if mail_key else "",
        )
        paths = save_account_record(record, output_dir=_output_dir.get())
        account = record.to_dict()
        account["_paths"] = {k: str(v) for k, v in paths.items()}
        banner(f"账号已保存：{mailbox.email}")
        return account

    with browser_session(proxy=proxy) as browser:
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
                link = poll.poll_magic_link(
                    to=mailbox.email, since=since, timeout=link_timeout
                )
                if link is None:
                    # 退一步试试 6 位码——Claude 的 UI 里存在这条路径。超时从
                    # --login-timeout 的总预算里扣除，不能在它之外再固定多等一段。
                    log(f"未收到登录链接，改试 6 位验证码（最多 {fallback_timeout:.0f}s）。")
                    code = poll.poll_code(
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
                        # 硬等 3 秒经常截到转圈页；改为等到 onboarding/主界面再继续。
                        ok = finish_after_auth(page, display_name=display_name)
                        if not ok:
                            log("登录后未能自动完成建号，请查看浏览器当前页面。")
                        # 注册收尾后（成功或已部分登录）都尝试导出 sessionKey
                        _capture(page)
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
                            log("完成拖拽后若停在建号页，请手动勾选条款并点 Create account。")
                            log(f"当前地址：{page.url}")
                        else:
                            ok = finish_after_auth(page, display_name=display_name)
                            if not ok:
                                log("验证码登录后未能自动完成建号，请查看浏览器当前页面。")
                            _capture(page)
                else:
                    log("既没收到登录链接，也没收到验证码。")
                    screenshot(page, "no_mail.png")
                    _report_manual_fallback(mailbox, poll)
            except Exception as exc:
                # 到这一步，链接/验证码已经从邮箱里取出来了——而且不能重新获取
                # （魔术链接一次性；邮箱不会再收到新邮件）。哪怕这里的收尾动作
                # （截图、读 URL 之类）出错，也绝不能让异常直接冲到 finally 把浏览器
                # 关掉，那样等于凭空浪费一个可能已经登录成功、无法再拿一次的凭证。
                log(f"登录后续操作出错（{exc}），但凭证已经取出且无法重新获取，"
                    "浏览器会保持打开，请手动检查当前页面状态。")
                try:
                    if account is None:
                        _capture(page)
                except Exception:
                    pass

        finally:
            try:
                context.close()
            except Exception as exc:
                # 账号可能在上面 _capture() 里已经落盘成功——context.close() 本身
                # 崩溃（比如目标进程已死）绝不能把这次已经拿到的 account 变成异常，
                # 让外层 run() 误判成「未保存」而去撤销刚导出的子 key。
                log(f"关闭浏览器上下文出错（{exc}），忽略。")
            # browser 的关闭交给 browser_session 的上下文退出，这里不重复关。
    return account


def run(
    *,
    email: str | None = None,
    domain: str | None = None,
    auto_login: bool = True,
    code_timeout: float = 120.0,
    config: Config | None = None,
    password: str = "",
) -> dict | None:
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
        proxy = config.register_proxy or None
    else:
        client = AnyMailClient(domain=domain)
        expires_hours = None
        proxy = None

    # 代理校验放在建邮箱之前：非法代理应尽早失败，别浪费一个刚建好的 AnyMail 邮箱。
    # 不能只查 URL 格式——带认证的 socks5 还要经中继，中继构造时才会发现凭据超长
    # 之类的问题，那时邮箱已经建出来了。这里把两层校验都跑一遍。
    validate_proxy(proxy)

    mailbox, since = prepare_mailbox(
        client, email=email, domain=domain, expires_hours=expires_hours
    )
    log(f"本次邮箱：{mailbox.email} (id={mailbox.id or 'new'})")

    child = client.create_child_key(
        email=mailbox.email, expires_at=mailbox.expires_at
    )
    if child:
        poll_client = AnyMailClient(
            base_url=client.base_url,
            api_key=child.plaintext,
            domain=client.domain or None,
            code_regex=client.code_regex or None,
            timeout=client.timeout,
        )
        log("已派生本邮箱专用子 key（仅 emails:read），接码轮询改用子 key。")
    else:
        poll_client = client

    try:
        account = run_browser(
            client,
            mailbox,
            since,
            auto_login=auto_login,
            code_timeout=code_timeout,
            proxy=proxy,
            password=password,
            poll_client=poll_client,
            mail_key=child.plaintext if child else "",
        )
    except BaseException:
        # run_browser 抛异常时这里拿不到它内部的 account 变量，没有轻量通道能
        # 分辨「账号是否已落盘」，所以这条回收路径假设：run_browser 一旦异常
        # 退出就等于未保存成功。这个假设的主要反例（_capture 已成功但随后
        # context.close() 崩溃）已经在 run_browser 的 finally 里用 try/except
        # 挡掉——那种情况下 run_browser 会正常返回 account 而不再抛到这里。
        # 引入 nonlocal/闭包信号把「已保存」状态跨异常传出来目前不值得为这一个
        # 边缘场景增加复杂度。
        if child:
            client.delete_key(child.id)
            log("注册中断，已撤销本次派生的子 key。")
        raise

    if child and not (account and account.get("sessionKey")):
        client.delete_key(child.id)
        log("注册未成功，已撤销本次派生的子 key。")

    log("完成。")
    banner(f"邮箱：{mailbox.email}")
    if mailbox.id:
        log(f"邮箱 id：{mailbox.id}")
    if account and account.get("sessionKey"):
        sk = str(account["sessionKey"])
        log(f"sessionKey：{sk[:16]}…（共 {len(sk)} 字符，已写入 accounts 文件）")
    else:
        log("未保存 sessionKey（登录/建号可能未完成）。")
    log("提示：邮箱默认永久保留；若显式设置了正数有效期，到期会被 AnyMail 清理。")
    # Claude 魔术链接注册无独立密码；password 字段默认空，仅作导出占位。
    return account
