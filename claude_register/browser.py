"""Playwright 操作。不依赖 anymail —— 验证码作为参数传入。"""

from __future__ import annotations

import contextvars
import random
import re
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import unquote, urlsplit

from camoufox.sync_api import Camoufox
from playwright.sync_api import Page, expect

from claude_register.console import current_sink, log
from claude_register.socks_relay import SocksRelay

URL = "https://claude.ai/login"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"

# onboarding 单步「页面还在、控件却找不到」的最长容忍时间。按钮点下后会进 loading
# 态（文字被 spinner 顶掉），此时按名字定位必然落空，需要给请求飞行留出窗口；超过
# 这个时长仍无进展，才认定是真卡住。
_STEP_STALL_MS = 20_000
# 页面"什么都认不出来"时的最长容忍时间。客户端路由切换途中 DOM 会短暂空白，
# 此时探针全落空，但下一步其实马上就渲染出来了。
_BLANK_TRANSITION_MS = 10_000

# Playwright 的 toJugglerProxyOptions 只认这四个 scheme；碰上不认识的会静默降级成
# http 代理（`let type = "http"` 的默认分支），于是浏览器拿 HTTP CONNECT 去捅一个
# 非 HTTP 端口，对端不回包 → NS_ERROR_NET_TIMEOUT。宁可在这里明确拒绝。
_PLAYWRIGHT_SCHEMES = {"http", "https", "socks4", "socks5"}
# socks5h 是 curl 的写法（h = 由代理做 DNS）。Playwright 走 SOCKS5 时本来就是远端
# 解析，语义等价，归一化掉即可。
_SCHEME_ALIASES = {"socks5h": "socks5", "socks4a": "socks4"}

_output_dir: contextvars.ContextVar = contextvars.ContextVar("output_dir", default=OUTPUT_DIR)


def set_output_dir(path) -> contextvars.Token:
    return _output_dir.set(Path(path))


def mask_proxy(url: str) -> str:
    """把代理 URL 里的密码换成 ***，供报错/日志使用。

    这些字符串会被 runner 写进 output/runs/<id>/log.txt，而那个文件网页端可读。
    配置里的代理密码不该因为一次填错就落进日志。解析不出结构时整串打码——
    宁可信息少一点，也别把凭据漏出去。
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return "***"
    if not parts.password:
        return url
    return url.replace(f":{parts.password}@", ":***@", 1)


def parse_proxy(url: str | None) -> dict | None:
    """把代理 URL 解析成 Playwright 风格 proxy dict。

    空 → None（直连）。scheme/host/port 缺一、或 scheme 不是 Playwright 认识的那几个
    → ValueError（不静默降级，避免用户以为走了代理实际在裸奔、或者卡在无解的超时里）。
    """
    text = (url or "").strip()
    if not text:
        return None
    safe = mask_proxy(text)
    hint = (
        f"代理地址格式不对：{safe!r}。应形如 "
        "http://host:port、http://user:pass@host:port 或 socks5://host:port"
    )
    try:
        parts = urlsplit(text)
        port = parts.port  # 端口非数字时这里抛 ValueError
    except ValueError as exc:
        raise ValueError(hint) from exc
    if not parts.scheme or not parts.hostname or port is None:
        raise ValueError(hint)
    scheme = parts.scheme.lower()
    scheme = _SCHEME_ALIASES.get(scheme, scheme)
    if scheme not in _PLAYWRIGHT_SCHEMES:
        raise ValueError(
            f"不支持的代理协议 {parts.scheme!r}（来自 {safe!r}）。"
            f"浏览器只支持：{'、'.join(sorted(_PLAYWRIGHT_SCHEMES))}。"
        )
    proxy: dict = {"server": f"{scheme}://{parts.hostname}:{port}"}
    if scheme == "socks4" and (parts.username or parts.password):
        # SOCKS4 协议里没有用户名密码认证，Playwright 会把凭据默默丢掉。
        # 静默降级 = 用户以为在认证、实际裸连，还不如当场报错。
        raise ValueError(f"SOCKS4 不支持用户名密码认证，请改用 socks5（来自 {safe!r}）")
    if parts.username:
        proxy["username"] = unquote(parts.username)
    if parts.password:
        proxy["password"] = unquote(parts.password)
    return proxy


def screenshot(page: Page, name: str) -> Path:
    d = _output_dir.get()
    d.mkdir(parents=True, exist_ok=True)
    path = d / name
    page.screenshot(path=path, full_page=True)
    log(f"截图已保存：{path}")
    return path


def pick_headless() -> str | bool:
    """按平台选 headless 档位。

    "virtual" 就是 Xvfb（X11 虚拟帧缓冲）：camoufox 会 Popen 一个 Xvfb 进程再把
    DISPLAY 塞进环境变量。它在 virtdisplay.py 里 assert_linux() 拦掉非 Linux 平台
    ——Windows 的 camoufox.exe 是原生 Win32 构建，不走 X11，DISPLAY 对它没有意义。
    所以 virtual 不是「还没适配 Windows」，是概念上不存在。

    但 virtual 要解决的问题（无显示器的机器上不想用真 headless，指纹太弱）在有桌面的
    平台上本来就不存在：直接 headless=False 用真显示器，比 Xvfb 还真。于是：

        Linux + 有 Xvfb  → "virtual"  容器/无头服务器的既有路径
        Linux 无 Xvfb    → True       只剩真 headless，指纹弱一档但能跑
        Windows / macOS  → False      桌面就是显示器，开真窗口

    判 Linux 用 sys.platform 而不是只查 which("Xvfb")：camoufox 拦的是
    OS_NAME != 'lin'，装了 WSL/Cygwin 的 Windows 上 which 可能真的命中一个
    Xvfb.exe，那时选 virtual 依然会崩。
    """
    if sys.platform.startswith("linux"):
        return "virtual" if shutil.which("Xvfb") else True
    return False


def needs_relay(proxy_cfg: dict | None) -> bool:
    """带凭据的 SOCKS5 需要本地中继。

    Firefox 不支持 SOCKS5 用户名密码认证，playwright driver 里直接抛
    "Browser does not support socks5 proxy authentication"。HTTP/HTTPS 代理的
    认证是支持的，无凭据的 socks5 也没问题——那些不必多绕一层。
    """
    if not proxy_cfg:
        return False
    if not proxy_cfg["server"].startswith("socks5://"):
        return False
    return bool(proxy_cfg.get("username") or proxy_cfg.get("password"))


def normalize_proxy_url(url: str | None) -> str | None:
    """把代理 URL 归一化成中继能直接用的形式（去空白、scheme 别名折叠）。

    中继必须拿归一化后的串：直接用配置原文的话，`  socks5h://…  ` 这种写法在
    parse_proxy 和中继两边解析结果不一致，出问题极难排查。
    """
    text = (url or "").strip()
    if not text:
        return None
    parts = urlsplit(text)
    scheme = _SCHEME_ALIASES.get(parts.scheme.lower(), parts.scheme.lower())
    auth = ""
    if parts.username:
        auth = parts.username
        if parts.password:
            auth += f":{parts.password}"
        auth += "@"
    return f"{scheme}://{auth}{parts.hostname}:{parts.port}"


def validate_proxy(url: str | None) -> dict | None:
    """完整校验代理配置，返回 parse_proxy 的结果。

    比 parse_proxy 多一步：带认证的 socks5 要经本地中继，中继对凭据长度等还有
    自己的约束。调用方（flow.run）需要在建邮箱之前就知道这个代理到底能不能用，
    否则等到启动浏览器才发现，已经白建了一个 AnyMail 邮箱。
    """
    cfg = parse_proxy(url)
    if needs_relay(cfg):
        SocksRelay(normalize_proxy_url(url))  # 只构造不 start()，触发凭据校验
    return cfg


def build_camoufox_kwargs(proxy: str | None) -> tuple[dict, "SocksRelay | None", str | bool]:
    """把代理配置转成 Camoufox 的 proxy kwargs，并决定 geoip。

    注册会话与接管会话共用这段：解析代理 → 带认证 SOCKS5 起本地中继 →
    用出口 IP 对齐 geoip → 组 kwargs。返回 (kwargs, relay, geoip)，
    relay 需由调用方在会话结束时 stop()。
    """
    proxy_cfg = parse_proxy(proxy)
    relay = None
    kwargs: dict = {}
    # geoip 让指纹（时区/地理）跟代理出口 IP 对齐。True 表示让 camoufox 自己去探测，
    # 但它那次探测走本地 DNS——本地被 fake-ip 污染时（Clash 一类透明代理把域名解析成
    # 198.18.x.x）会拿虚拟地址去 CONNECT，上游认不得直接关连接，启动就崩了。
    # 所以只要中继能查到出口 IP，就直接把 IP 喂给它，跳过那次探测。
    geoip: str | bool = True
    if proxy_cfg is not None:
        if needs_relay(proxy_cfg):
            # on_error 是在中继自己的线程里回调的，而 console 的 sink 是 ContextVar
            # ——新线程起来时上下文是空的，日志会直接打到 stdout，网页端的 log.txt
            # 里什么都看不到。这里在当前上下文里把 sink 取出来，回调时直接用。
            #
            # 不用 contextvars.copy_context()：Context 不可重入，两个 handler 线程
            # 同时报错时后来的那个会撞上 "is already entered"，日志反而丢得更多——
            # 而并发报错恰恰是撞上游限额时的常态。
            relay_sink = current_sink()

            def _relay_log(msg: str) -> None:
                relay_sink(f"代理中继：{msg}")

            # 中继起不来是代理的问题，得当场说清楚。漏到下面那个 Camoufox 兜底
            # 里会变成「请先运行 camoufox fetch」，把人往完全无关的方向带。
            try:
                relay = SocksRelay(
                    normalize_proxy_url(proxy),
                    on_error=_relay_log,
                ).start()
            except Exception as exc:
                raise RuntimeError(
                    f"启动本地代理中继失败（{exc}）。请检查代理地址 "
                    f"{proxy_cfg['server']} 是否可达。"
                ) from exc
            kwargs["proxy"] = {"server": relay.local_url}
            log(f"使用代理：{proxy_cfg['server']}（带认证，经本地中继 {relay.local_url}）")
            exit_ip = relay.exit_ip()
            if exit_ip:
                geoip = exit_ip
                log(f"代理出口 IP：{exit_ip}")
            else:
                log("查不到代理出口 IP，交给 camoufox 自行探测。")
        else:
            kwargs["proxy"] = proxy_cfg
            log(f"使用代理：{proxy_cfg['server']}")
    return kwargs, relay, geoip


@contextmanager
def browser_session(proxy: str | None = None):
    """启动 Camoufox（Firefox 系隐身浏览器）会话。

    headless 档位由 pick_headless() 按平台自动选：Linux 容器走 "virtual"（Xvfb），
    Windows/macOS 走 False（桌面真显示器）。两者都比真 headless 更抗 Cloudflare 检测。
    humanize 提供人性化光标移动；locale/geoip 让指纹统一
    （配了代理时 geoip 按代理出口 IP 匹配时区/地理指纹）。

    带认证的 SOCKS5 会先在本地拉起一个免认证中继（见 socks_relay），
    浏览器只连 127.0.0.1，凭据由中继负责递给上游。
    """
    kwargs, relay, geoip = build_camoufox_kwargs(proxy)
    headless = pick_headless()
    cm = Camoufox(
        headless=headless,
        humanize=True,
        locale="en-US",
        geoip=geoip,
        window=(1280, 900),
        **kwargs,
    )
    # 真正的启动发生在 __enter__（拉起 Firefox / Xvfb），构造函数不会抛——所以只包
    # __enter__ 才能拦到「没 fetch 二进制」「缺 Xvfb」这类启动失败，并给出可操作的提示。
    # 不能用 `with` 把 yield 也裹进 try，否则调用方 body 里的页面异常会被误报成启动失败。
    try:
        browser = cm.__enter__()
    except Exception as exc:
        if relay is not None:
            relay.stop()
        # Xvfb 只在真的走 virtual 时才相关。Windows 上装 Xvfb 没有任何用——
        # camoufox.exe 不走 X11——这句提示会把人往完全错的方向带。
        extra = "，并确认已安装 Xvfb" if headless == "virtual" else ""
        raise RuntimeError(
            f"启动 Camoufox 失败（{exc}）。请先运行 `uv run camoufox fetch` "
            f"下载浏览器二进制{extra}。"
        ) from exc
    log(f"已启动 Camoufox（headless={headless}）")
    try:
        yield browser
    finally:
        cm.__exit__(None, None, None)
        if relay is not None:
            relay.stop()


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

def terms_create_visible(page: Page) -> bool:
    """是否停在「Let's create your account」条款建号页。"""
    for build in (
        lambda: page.get_by_role("heading", name="Let's create your account"),
        lambda: page.get_by_text("Let's create your account", exact=False),
        lambda: page.get_by_role("button", name="Create account"),
    ):
        try:
            loc = build()
            if loc.count() >= 1 and loc.first.is_visible():
                return True
        except Exception:
            continue
    return False


def team_join_visible(page: Page) -> bool:
    """是否停在「Join your team」页（邮箱域名已有 Team 时出现）。

    实测截图：提示 Your team at {domain} is already on Claude，
    选项是 Join 或 Continue with personal account。注册个人号走后者。
    """
    for build in (
        lambda: page.get_by_role("heading", name="Join your team"),
        lambda: page.get_by_text("Join your team", exact=False),
        lambda: page.get_by_role("button", name="Continue with personal account"),
        lambda: page.get_by_text("Continue with personal account", exact=False),
    ):
        try:
            loc = build()
            if loc.count() >= 1 and loc.first.is_visible():
                return True
        except Exception:
            continue
    return False


def onboarding_visible(page: Page) -> bool:
    """是否仍在 onboarding 流程（条款 / 团队 / 用途 / 套餐 / 桌面端 / 首聊前须知 / 名字 / 角色）。"""
    return (
        terms_create_visible(page)
        or team_join_visible(page)
        or use_case_visible(page)
        or plan_select_visible(page)
        or desktop_promo_visible(page)
        or first_chat_intro_visible(page)
        or name_step_visible(page)
        or work_role_visible(page)
    )


def continue_with_personal_account(page: Page) -> bool:
    """在 Join your team 页选择个人账号，不加入域名团队。

    按文字定位的三条策略在按钮进 loading 态时会全部落空（文字被 spinner 顶掉），
    故补一条 test-id 兜底。
    """
    for build in (
        lambda: page.get_by_role("button", name="Continue with personal account"),
        lambda: page.get_by_text("Continue with personal account", exact=True),
        lambda: page.get_by_text("Continue with personal account", exact=False),
        lambda: page.get_by_test_id("continue-with-personal-account"),
    ):
        try:
            loc = build()
            if loc.count() >= 1 and loc.first.is_visible():
                loc.first.click()
                log("已点击 Continue with personal account")
                return True
        except Exception as exc:
            log(f"点击 Continue with personal account 失败（{exc}）。")
            continue
    log("未找到 Continue with personal account 按钮。")
    return False


def use_case_visible(page: Page) -> bool:
    """是否停在「How are you planning to use Claude?」用途选择页。"""
    for build in (
        lambda: page.get_by_role("heading", name="How are you planning to use Claude?"),
        lambda: page.get_by_text("How are you planning to use Claude?", exact=False),
        lambda: page.get_by_text("For personal use", exact=True),
        lambda: page.get_by_text("With my team", exact=True),
    ):
        try:
            loc = build()
            if loc.count() >= 1 and loc.first.is_visible():
                return True
        except Exception:
            continue
    return False


def select_personal_use(page: Page) -> bool:
    """用途选择页点 For personal use（不要选 With my team）。"""
    for build in (
        lambda: page.get_by_role("button", name="For personal use"),
        lambda: page.get_by_role("radio", name="For personal use"),
        lambda: page.get_by_text("For personal use", exact=True),
        # 卡片可能是可点击的 div/article，用包含标题的区域
        lambda: page.locator("button, [role='button'], [role='radio'], label, div").filter(
            has_text="For personal use"
        ),
    ):
        try:
            loc = build()
            if loc.count() >= 1 and loc.first.is_visible():
                loc.first.click()
                log("已选择 For personal use")
                return True
        except Exception as exc:
            log(f"点击 For personal use 失败（{exc}）。")
            continue
    log("未找到 For personal use 选项。")
    return False


def plan_select_visible(page: Page) -> bool:
    """是否停在套餐选择页「Plans that grow with you」。"""
    for build in (
        lambda: page.get_by_role("heading", name="Plans that grow with you"),
        lambda: page.get_by_text("Plans that grow with you", exact=False),
        lambda: page.get_by_role("button", name="Use Claude for free"),
        lambda: page.get_by_text("Use Claude for free", exact=True),
    ):
        try:
            loc = build()
            if loc.count() >= 1 and loc.first.is_visible():
                return True
        except Exception:
            continue
    return False


def select_free_plan(page: Page) -> bool:
    """套餐页选最左侧 Free：Use Claude for free（不要 Pro/Max）。"""
    for build in (
        lambda: page.get_by_role("button", name="Use Claude for free"),
        lambda: page.get_by_text("Use Claude for free", exact=True),
        lambda: page.get_by_text("Use Claude for free", exact=False),
    ):
        try:
            loc = build()
            if loc.count() >= 1 and loc.first.is_visible():
                loc.first.click()
                log("已选择 Use Claude for free")
                return True
        except Exception as exc:
            log(f"点击 Use Claude for free 失败（{exc}）。")
            continue
    log("未找到 Use Claude for free 按钮。")
    return False


def desktop_promo_visible(page: Page) -> bool:
    """是否停在桌面端推广页「Get the most out of Claude on your desktop」。"""
    for build in (
        lambda: page.get_by_role(
            "heading", name="Get the most out of Claude on your desktop"
        ),
        lambda: page.get_by_text(
            "Get the most out of Claude on your desktop", exact=False
        ),
        lambda: page.get_by_role("button", name="Download for Windows"),
        lambda: page.get_by_text("Download for Windows", exact=False),
    ):
        try:
            loc = build()
            if loc.count() >= 1 and loc.first.is_visible():
                return True
        except Exception:
            continue
    return False


def skip_desktop_promo(page: Page) -> bool:
    """桌面端推广页点 Skip，不下载客户端。"""
    for build in (
        lambda: page.get_by_role("button", name="Skip"),
        lambda: page.get_by_role("link", name="Skip"),
        lambda: page.get_by_text("Skip", exact=True),
    ):
        try:
            loc = build()
            if loc.count() >= 1 and loc.first.is_visible():
                loc.first.click()
                log("已点击 Skip（跳过桌面端下载）")
                return True
        except Exception as exc:
            log(f"点击 Skip 失败（{exc}）。")
            continue
    log("未找到 Skip 按钮。")
    return False


def first_chat_intro_visible(page: Page) -> bool:
    """是否停在「Before your first chat」须知页。"""
    for build in (
        lambda: page.get_by_role("heading", name="Before your first chat"),
        lambda: page.get_by_text("Before your first chat", exact=False),
        lambda: page.get_by_text("Help improve our AI models", exact=False),
        lambda: page.get_by_text("Ad-free chats", exact=False),
    ):
        try:
            loc = build()
            if loc.count() >= 1 and loc.first.is_visible():
                return True
        except Exception:
            continue
    return False


def continue_first_chat_intro(page: Page) -> bool:
    """首聊前须知页点 Continue。

    「Help improve our AI models」开关保持页面默认即可（截图里多为 Off），
    这里只点 Continue 进入下一步/主界面。
    """
    # 优先：该页底部主按钮 Continue（不要误点别处的 Continue with ...）
    for build in (
        lambda: page.get_by_role("button", name="Continue", exact=True),
        lambda: page.get_by_role("button", name="Continue"),
        lambda: page.get_by_text("Continue", exact=True),
    ):
        try:
            loc = build()
            n = loc.count()
            if n < 1:
                continue
            # 若有多个 Continue，选可见的、文案恰好是 Continue 的
            for i in range(n):
                btn = loc.nth(i) if hasattr(loc, "nth") else loc.first
                try:
                    if not btn.is_visible():
                        continue
                    text = ""
                    try:
                        text = (btn.inner_text() or "").strip()
                    except Exception:
                        text = ""
                    if text and text != "Continue":
                        continue
                    btn.click()
                    log("已点击 Continue（Before your first chat）")
                    return True
                except Exception:
                    continue
        except Exception as exc:
            log(f"点击 Continue 失败（{exc}）。")
            continue
    log("未找到 Continue 按钮。")
    return False


def chat_home_visible(page: Page) -> bool:
    """是否已进入 Claude 主聊天界面（老账号登录，或建号完成后）。"""
    for build in (
        lambda: page.get_by_placeholder("How can I help you today?"),
        lambda: page.get_by_placeholder("Reply to Claude"),
        lambda: page.get_by_role("button", name="New chat"),
        lambda: page.get_by_test_id("chat-input"),
        lambda: page.locator("[data-testid='chat-input']"),
        lambda: page.locator("fieldset textarea, div[contenteditable='true']"),
    ):
        try:
            loc = build()
            if loc.count() >= 1 and loc.first.is_visible():
                return True
        except Exception:
            continue
    return False


def wait_post_auth(page: Page, timeout_ms: int = 90_000) -> str:
    """魔术链接/验证码提交后，等引导页或主界面就绪。

    返回 "onboarding" / "chat" / "unknown"。
    onboarding 包括条款建号页和 Join your team 页。
    """
    step = 2_000
    waited = 0
    while waited < timeout_ms:
        try:
            if team_join_visible(page):
                log("检测到 Join your team 页。")
                return "onboarding"
            if use_case_visible(page):
                log("检测到用途选择页。")
                return "onboarding"
            if plan_select_visible(page):
                log("检测到套餐选择页。")
                return "onboarding"
            if desktop_promo_visible(page):
                log("检测到桌面端推广页。")
                return "onboarding"
            if first_chat_intro_visible(page):
                log("检测到首聊前须知页。")
                return "onboarding"
            if name_step_visible(page):
                log("检测到名字填写页。")
                return "onboarding"
            if work_role_visible(page):
                log("检测到工作角色选择页。")
                return "onboarding"
            if terms_create_visible(page):
                log("建号引导页已出现。")
                return "onboarding"
            if chat_home_visible(page):
                log("已进入 Claude 主界面。")
                return "chat"
            current_url = page.url
        except Exception as exc:
            log(f"等待登录完成时页面不可用（{exc}），停止等待。")
            return "unknown"
        log(f"等待登录完成… {waited // 1000}s url={current_url}")
        try:
            page.wait_for_timeout(step)
        except Exception as exc:
            log(f"等待登录完成期间页面失效（{exc}）。")
            return "unknown"
        waited += step
    log("登录完成后既未出现建号页，也未进入主界面。")
    return "unknown"


def _terms_checkbox(page: Page):
    """定位服务条款复选框（方框本身，不是条款链接）。

    实测：点「Consumer Terms / Acceptable Use Policy」链接不会勾选，
    只会跳文档或啥也不做；必须点左侧 checkbox 控件。
    """
    for build in (
        lambda: page.get_by_role("checkbox"),
        lambda: page.locator("[role='checkbox']"),
        lambda: page.locator("input[type='checkbox']"),
        # 有的实现把可点方框做成 button / 无 role 的 span
        lambda: page.locator("label").filter(
            has_text="I agree to Anthropic"
        ).locator("[role='checkbox'], input[type='checkbox'], button, span").first,
        lambda: page.locator("[data-state]").filter(has_text="I agree").first,
    ):
        try:
            loc = build()
            # locator.first 没有 count 语义时走 is_visible
            try:
                count = loc.count()
            except Exception:
                count = 1
            if count >= 1:
                target = loc.first if hasattr(loc, "first") else loc
                try:
                    if target.is_visible():
                        return target
                except Exception:
                    return target
        except Exception:
            continue
    return None


def _terms_is_checked(page: Page, box=None) -> bool:
    """确认条款已被勾选。自定义组件可能没有原生 checked，要看 aria/data-state。"""
    candidates = []
    if box is not None:
        candidates.append(box)
    try:
        loc = page.get_by_role("checkbox")
        if loc.count() >= 1:
            candidates.append(loc.first)
    except Exception:
        pass
    try:
        loc = page.locator("[role='checkbox'], input[type='checkbox']")
        if loc.count() >= 1:
            candidates.append(loc.first)
    except Exception:
        pass

    for cand in candidates:
        try:
            if bool(cand.is_checked()):
                return True
        except Exception:
            pass
        for attr, ok in (
            ("aria-checked", "true"),
            ("data-state", "checked"),
            ("data-checked", "true"),
            ("aria-pressed", "true"),
        ):
            try:
                val = cand.get_attribute(attr)
                if val is not None and val.lower() == ok:
                    return True
            except Exception:
                continue
        try:
            cls = cand.get_attribute("class") or ""
            if "checked" in cls.lower():
                return True
        except Exception:
            pass
    return False


def _click_terms_checkbox(page: Page, box) -> bool:
    """想办法把方框点上。避免点到条款 <a> 链接。"""
    # 1) 原生 check()
    try:
        box.check(force=True)
        if _terms_is_checked(page, box):
            return True
    except Exception:
        pass

    # 2) 点控件本身（左上角，躲开右侧文字/链接）
    try:
        box.click(force=True, position={"x": 4, "y": 4})
        page.wait_for_timeout(200)
        if _terms_is_checked(page, box):
            return True
    except Exception:
        pass
    try:
        box.click(force=True)
        page.wait_for_timeout(200)
        if _terms_is_checked(page, box):
            return True
    except Exception:
        pass

    # 3) 键盘：聚焦后空格切换
    try:
        box.focus()
        page.keyboard.press("Space")
        page.wait_for_timeout(200)
        if _terms_is_checked(page, box):
            return True
    except Exception:
        pass

    # 4) JS：直接点 checkbox / 设 aria，并派发事件
    try:
        ok = page.evaluate(
            """() => {
                const sel = [
                    '[role="checkbox"]',
                    'input[type="checkbox"]',
                    'label',
                ];
                let el = null;
                for (const s of sel) {
                    const nodes = Array.from(document.querySelectorAll(s));
                    el = nodes.find(n => {
                        const t = (n.innerText || n.textContent || '').toLowerCase();
                        const isBox = n.getAttribute('role') === 'checkbox'
                            || (n.tagName === 'INPUT' && n.type === 'checkbox');
                        if (isBox) return true;
                        return t.includes('i agree') || t.includes('acceptable use');
                    }) || null;
                    if (el) break;
                }
                if (!el) return false;
                // 若拿到的是 label，优先点里面的 checkbox 控件
                const box = el.matches('[role="checkbox"], input[type="checkbox"]')
                    ? el
                    : (el.querySelector('[role="checkbox"], input[type="checkbox"]') || el);
                box.click();
                if (box.tagName === 'INPUT') {
                    box.checked = true;
                    box.dispatchEvent(new Event('input', { bubbles: true }));
                    box.dispatchEvent(new Event('change', { bubbles: true }));
                } else {
                    box.setAttribute('aria-checked', 'true');
                    box.setAttribute('data-state', 'checked');
                    box.dispatchEvent(new Event('click', { bubbles: true }));
                }
                return true;
            }"""
        )
        page.wait_for_timeout(200)
        if ok and _terms_is_checked(page, box):
            return True
        # JS 点了但属性检测仍失败——有的组件用内部 state，
        # 再信一次「至少点过」由调用方结合错误提示重试。
        if ok:
            return True
    except Exception:
        pass

    return False


def _agree_error_visible(page: Page) -> bool:
    """点 Create 但没勾选时的红字：Agree to the terms to continue。"""
    for text in (
        "Agree to the terms to continue",
        "Agree to the terms",
    ):
        try:
            loc = page.get_by_text(text, exact=False)
            if loc.count() >= 1 and loc.first.is_visible():
                return True
        except Exception:
            continue
    return False


def accept_terms_and_create_account(page: Page) -> bool:
    """勾选 Anthropic 条款并点击 Create account。

    对应截图：claude.ai/onboarding 上的
    「I agree to Anthropic's Consumer Terms...」+「Create account」。
    未勾选就点 Create 会出红字 Agree to the terms to continue——
    所以这里必须先验证勾选状态，失败就重试，绝不「以为点了就算了」。
    """
    box = _terms_checkbox(page)

    checked = False
    if box is not None and _terms_is_checked(page, box):
        checked = True
        log("服务条款原本已勾选。")
    elif box is not None:
        for attempt in range(1, 4):
            log(f"尝试勾选服务条款（第 {attempt} 次）…")
            if _click_terms_checkbox(page, box):
                # 再读一次，自定义组件可能延迟更新
                page.wait_for_timeout(300)
                if _terms_is_checked(page, box) or attempt >= 2:
                    # attempt>=2：JS 路径可能改了内部 state 但属性读不到，
                    # 交给后面点 Create 后的红字检测兜底。
                    checked = True
                    log("已勾选服务条款。")
                    break
            page.wait_for_timeout(300)
    else:
        # 找不到 checkbox 控件时，点 label 左侧（避开链接）
        log("未定位到 checkbox 控件，改点条款行左侧。")
        for build in (
            lambda: page.locator("label").filter(has_text="I agree to Anthropic"),
            lambda: page.get_by_text("I agree to Anthropic", exact=False),
        ):
            try:
                loc = build()
                if loc.count() >= 1 and loc.first.is_visible():
                    loc.first.click(position={"x": 8, "y": 8}, force=True)
                    page.wait_for_timeout(300)
                    checked = True
                    log("已通过条款行左侧点击尝试勾选。")
                    break
            except Exception as exc:
                log(f"点击条款行失败（{exc}）。")
                continue

    if not checked:
        log("无法勾选服务条款复选框。")
        return False

    try:
        page.wait_for_timeout(400)
    except Exception:
        pass

    # 点 Create account；若冒出红字则重新勾选再点一次
    for round_i in range(1, 4):
        try:
            btn = page.get_by_role("button", name="Create account")
            if btn.count() < 1:
                btn = page.get_by_text("Create account", exact=True)
            if btn.count() < 1 or not btn.first.is_visible():
                log("未找到 Create account 按钮。")
                return False
            target = btn.first
            try:
                expect(target).to_be_enabled(timeout=10_000)
            except Exception:
                pass
            target.click()
            log(f"已点击 Create account（第 {round_i} 次）")
        except Exception as exc:
            log(f"点击 Create account 失败（{exc}）。")
            return False

        try:
            page.wait_for_timeout(800)
        except Exception:
            pass

        if not _agree_error_visible(page):
            return True

        log("页面提示 Agree to the terms to continue——勾选未生效，重试。")
        box = _terms_checkbox(page) or box
        if box is not None:
            _click_terms_checkbox(page, box)
            page.wait_for_timeout(400)
        else:
            try:
                page.locator("label").filter(
                    has_text="I agree to Anthropic"
                ).first.click(position={"x": 8, "y": 8}, force=True)
            except Exception:
                pass

    log("多次尝试后仍无法通过条款勾选。")
    return False


_DISPLAY_FIRST_NAMES = (
    "Alex",
    "Sam",
    "Jordan",
    "Taylor",
    "Casey",
    "Riley",
    "Morgan",
    "Quinn",
    "Avery",
    "Jamie",
    "Cameron",
    "Reese",
)


def default_display_name(email: str | None = None) -> str:
    """生成看起来像真人的显示名；邮箱 local 不像人名时随机挑一个。"""
    if email:
        local = (email.split("@", 1)[0] or "").strip()
        cleaned = re.sub(r"[0-9._+-]+", " ", local)
        parts = [p for p in cleaned.split() if p.isalpha() and len(p) >= 3]
        skip = {"claude", "user", "test", "mail", "email", "admin", "info", "xyz"}
        for part in parts:
            if part.lower() not in skip:
                return part[:1].upper() + part[1:].lower()
    return random.choice(_DISPLAY_FIRST_NAMES)


def name_step_visible(page: Page) -> bool:
    """是否停在「What's your name?」填写页。"""
    for build in (
        lambda: page.get_by_role("heading", name="What's your name?"),
        lambda: page.get_by_text("What's your name?", exact=False),
        lambda: page.get_by_text("So Claude knows what to call you", exact=False),
        lambda: page.get_by_placeholder("Enter your name"),
    ):
        try:
            loc = build()
            if loc.count() >= 1 and loc.first.is_visible():
                return True
        except Exception:
            continue
    return False


def _name_input(page: Page):
    for build in (
        lambda: page.get_by_placeholder("Enter your name"),
        lambda: page.get_by_label("Preferred name"),
        lambda: page.get_by_label("What should we call you"),
        lambda: page.get_by_label("Your name"),
        lambda: page.get_by_role("textbox", name="Enter your name"),
        lambda: page.locator("input[name='name']"),
        lambda: page.locator("input[autocomplete='name']"),
        lambda: page.locator("input[type='text']"),
    ):
        try:
            loc = build()
            if loc.count() >= 1 and loc.first.is_visible():
                return loc.first
        except Exception:
            continue
    return None


def _click_name_continue(page: Page) -> bool:
    """名字页底部 Continue：填名前是 disabled 灰色，需等可点。"""
    for build in (
        lambda: page.get_by_role("button", name="Continue", exact=True),
        lambda: page.get_by_role("button", name="Continue"),
        lambda: page.get_by_role("button", name="Next", exact=True),
        lambda: page.get_by_test_id("continue"),
    ):
        try:
            loc = build()
            n = loc.count()
            if n < 1:
                continue
            for i in range(n):
                btn = loc.nth(i) if hasattr(loc, "nth") else loc.first
                try:
                    if not btn.is_visible():
                        continue
                    text = ""
                    try:
                        text = (btn.inner_text() or "").strip()
                    except Exception:
                        text = ""
                    if text and text not in {"Continue", "Next"}:
                        continue
                    for _ in range(30):
                        enabled = True
                        try:
                            enabled = btn.is_enabled()
                        except Exception:
                            enabled = True
                        if enabled:
                            break
                        page.wait_for_timeout(100)
                    try:
                        if hasattr(btn, "is_enabled") and not btn.is_enabled():
                            continue
                    except Exception:
                        pass
                    btn.click()
                    log("已点击 Continue（What's your name?）")
                    return True
                except Exception:
                    continue
        except Exception as exc:
            log(f"点击名字页 Continue 失败（{exc}）。")
            continue
    return False


def fill_display_name_and_continue(page: Page, name: str | None = None) -> bool:
    """在 What's your name? 页填入名字并点 Continue。"""
    display = (name or "").strip() or default_display_name()
    box = _name_input(page)
    if box is None:
        log("未找到名字输入框。")
        return False
    try:
        box.click()
        try:
            box.fill("")
        except Exception:
            pass
        try:
            box.press_sequentially(display, delay=40)
        except Exception:
            box.fill(display)
        log(f"已填入显示名：{display}")
        page.wait_for_timeout(300)
        if _click_name_continue(page):
            return True
        try:
            box.press("Enter")
            log("已对名字输入框按 Enter 提交。")
            return True
        except Exception:
            pass
        log("已填名字但未能点击 Continue。")
        return False
    except Exception as exc:
        log(f"填写显示名失败（{exc}）。")
        return False


def maybe_fill_display_name(page: Page, name: str | None = None) -> bool:
    """兼容旧调用：仅当名字页可见时填写并 Continue。

    没有该步骤时返回 False，不算失败。
    """
    if not name_step_visible(page) and _name_input(page) is None:
        return False
    return fill_display_name_and_continue(page, name=name)


def work_role_visible(page: Page) -> bool:
    """是否停在「What kind of work do you do?」角色选择页。"""
    for build in (
        lambda: page.get_by_role("heading", name="What kind of work do you do?"),
        lambda: page.get_by_text("What kind of work do you do?", exact=False),
        lambda: page.get_by_text("Pick a role so Claude can tailor your experience", exact=False),
        lambda: page.get_by_text("Select your role", exact=False),
        lambda: page.get_by_text("Set up later", exact=True),
    ):
        try:
            loc = build()
            if loc.count() >= 1 and loc.first.is_visible():
                return True
        except Exception:
            continue
    return False


def skip_work_role(page: Page) -> bool:
    """角色选择页点 Set up later，不选具体 role。"""
    for build in (
        lambda: page.get_by_role("button", name="Set up later"),
        lambda: page.get_by_role("link", name="Set up later"),
        lambda: page.get_by_text("Set up later", exact=True),
        lambda: page.get_by_text("Set up later", exact=False),
    ):
        try:
            loc = build()
            if loc.count() >= 1 and loc.first.is_visible():
                loc.first.click()
                log("已点击 Set up later（工作角色）")
                return True
        except Exception as exc:
            log(f"点击 Set up later 失败（{exc}）。")
            continue
    log("未找到 Set up later。")
    return False



def extract_session_key(page: Page) -> str | None:
    """从浏览器 cookie 读取 Claude sessionKey。"""
    try:
        context = page.context
    except Exception:
        return None
    try:
        cookies = context.cookies()
    except Exception as exc:
        log(f"读取 cookies 失败（{exc}）。")
        return None
    # 优先精确名；兼容大小写 / 旧字段
    wanted = {"sessionkey", "session_key"}
    found = None
    for c in cookies or []:
        name = str(c.get("name") or "")
        if name.lower() in wanted:
            val = str(c.get("value") or "").strip()
            if val:
                # sessionKey 优于其它别名
                if name == "sessionKey":
                    return val
                found = found or val
    return found


def wait_for_session_key(page: Page, timeout_ms: int = 30_000) -> str | None:
    """登录/建号完成后轮询 sessionKey cookie。"""
    step = 1_000
    waited = 0
    while waited <= timeout_ms:
        key = extract_session_key(page)
        if key:
            log(f"已获取 sessionKey（{len(key)} chars）。")
            return key
        if waited >= timeout_ms:
            break
        try:
            page.wait_for_timeout(step)
        except Exception:
            break
        waited += step
    log("超时仍未在 cookies 中找到 sessionKey。")
    return None


def dump_cookies(page: Page) -> list[dict]:
    """调试用：导出当前 context 全部 cookie（不含落盘）。"""
    try:
        return list(page.context.cookies() or [])
    except Exception:
        return []


def _still_on_onboarding_url(page: Page) -> bool:
    """URL 是否还在 onboarding 路径下。

    DOM 探针在客户端路由切换途中会全部落空（页面短暂空白），此时 URL 是比 DOM
    更可靠的信号：还挂在 /onboarding 就说明没走完。
    """
    try:
        return "/onboarding" in urlsplit(page.url).path
    except Exception:
        return False


def _onboarding_steps(display_name: str | None) -> list[dict]:
    """onboarding 各步：判定 / 动作 / 截图 / 放弃时的话术。顺序即优先级。

    每步形状一致，逐个 if 抄八遍只会让「某一步漏改」变成必然，故收成表。
    """
    return [
        {
            "key": "team_join",
            "label": "Join your team",
            "visible": team_join_visible,
            "act": continue_with_personal_account,
            "shot": "team_join.png",
            "fail_shot": "team_join_failed.png",
            "fail_msg": "无法跳过 Join your team，请手动点 Continue with personal account。",
        },
        {
            "key": "use_case",
            "label": "用途选择",
            "visible": use_case_visible,
            "act": select_personal_use,
            "shot": "use_case.png",
            "fail_shot": "use_case_failed.png",
            "fail_msg": "无法选择 For personal use，请手动点击。",
        },
        {
            "key": "plan_select",
            "label": "套餐选择",
            "visible": plan_select_visible,
            "act": select_free_plan,
            "shot": "plan_select.png",
            "fail_shot": "plan_select_failed.png",
            "fail_msg": "无法选择 Use Claude for free，请手动点击。",
        },
        {
            "key": "desktop_promo",
            "label": "桌面端推广",
            "visible": desktop_promo_visible,
            "act": skip_desktop_promo,
            "shot": "desktop_promo.png",
            "fail_shot": "desktop_promo_failed.png",
            "fail_msg": "无法跳过桌面端推广，请手动点 Skip。",
        },
        {
            "key": "first_chat",
            "label": "首聊前须知",
            "visible": first_chat_intro_visible,
            "act": continue_first_chat_intro,
            "shot": "first_chat_intro.png",
            "fail_shot": "first_chat_intro_failed.png",
            "fail_msg": "无法点击 Continue（Before your first chat），请手动点。",
        },
        {
            "key": "name_step",
            "label": "名字填写",
            "visible": name_step_visible,
            "act": lambda p: fill_display_name_and_continue(
                p, name=display_name or default_display_name()
            ),
            "shot": "name_step.png",
            "fail_shot": "name_step_failed.png",
            "fail_msg": "无法填写名字并 Continue，请手动完成。",
        },
        {
            "key": "work_role",
            "label": "工作角色选择",
            "visible": work_role_visible,
            "act": skip_work_role,
            "shot": "work_role.png",
            "fail_shot": "work_role_failed.png",
            "fail_msg": "无法点击 Set up later，请手动点。",
        },
        {
            "key": "terms",
            "label": "条款建号",
            "visible": terms_create_visible,
            "act": accept_terms_and_create_account,
            "shot": None,  # 进 onboarding 时已截过 onboarding.png
            "fail_shot": "onboarding_failed.png",
            "fail_msg": "无法完成条款建号，请手动点 Create account。",
        },
    ]


def finish_after_auth(page: Page, timeout_ms: int = 90_000, display_name: str | None = None) -> bool:
    """登录凭证生效后的收尾：等页面 → 走完 onboarding 各步 → 截图。

    新账号常见顺序（多步顺序不固定）：
      Join your team → Continue with personal account
      How are you planning to use Claude? → For personal use
      Plans that grow with you → Use Claude for free
      Get the most out of Claude on your desktop → Skip
      Before your first chat → Continue
      What's your name? → 填名字 → Continue
      What kind of work do you do? → Set up later
      Let's create your account → 勾条款 → Create account
      主界面

    老账号：直接落到主界面。
    """
    state = wait_post_auth(page, timeout_ms=timeout_ms)
    if state == "chat":
        screenshot(page, "logged_in.png")
        log(f"当前地址：{page.url}")
        return True

    if state != "onboarding":
        screenshot(page, "post_auth_unknown.png")
        try:
            log(f"当前地址：{page.url}")
        except Exception:
            pass
        return False

    screenshot(page, "onboarding.png")

    step = 2_000
    waited = 0
    # 整段 onboarding 共用剩余预算；至少留 60s 给多步点击。
    follow_timeout = max(60_000, timeout_ms)
    progressed = False

    steps = _onboarding_steps(display_name)
    # 每步的停滞账本：clicked=成功点过几次，ms=当前这轮找不到控件已经耗了多久。
    stalls: dict[str, dict[str, int]] = {}
    # 连续多少轮"什么都认不出来"——用来区分路由切换空白期和真的走完了。
    blank_rounds = 0

    while waited < follow_timeout:
        try:
            if chat_home_visible(page):
                log("建号完成，已进入主界面。")
                screenshot(page, "after_create_account.png")
                log(f"当前地址：{page.url}")
                return True

            handled = False
            for item in steps:
                if not item["visible"](page):
                    continue

                state = stalls.setdefault(item["key"], {"clicked": 0, "ms": 0, "shot": 0})
                # 只在首次进入该步时截图：等 spinner 期间每轮重截同名图纯属浪费，
                # 真卡住时另有 *_failed.png 记录终态。
                if item["shot"] and not state["shot"]:
                    screenshot(page, item["shot"])
                    state["shot"] = 1

                if item["act"](page):
                    if state["clicked"]:
                        log(f"仍在{item['label']}页，已重新点击。")
                    state["clicked"] += 1
                    state["ms"] = 0
                    progressed = True
                    page.wait_for_timeout(1_500)
                    waited += 1_500
                    handled = True
                    break

                # 找不到控件多半不是页面异常，而是上一次点击还在飞：按钮进 loading 态后
                # 文字被 spinner 顶掉，按名字定位必然落空，而 heading 还在 → 上层仍判定
                # "停在本页"。这里必须等它转完，不能一次没找到就判死（run 6 的坑）。
                if state["ms"] < _STEP_STALL_MS:
                    state["ms"] += step
                    log(f"{item['label']}处理中… {state['ms'] // 1000}s")
                    page.wait_for_timeout(step)
                    waited += step
                    handled = True
                    break

                screenshot(page, item["fail_shot"])
                log(item["fail_msg"])
                return False

            if handled:
                continue

            # 3) 兜底：未命中 name_step_visible 但出现名字输入框
            if maybe_fill_display_name(page, name=display_name):
                progressed = True
                page.wait_for_timeout(1_000)
                waited += 1_000
                continue

            # 已经推进过、又不再识别为 onboarding——可能真跳转了，也可能只是客户端
            # 路由切换途中 DOM 短暂空白（run 7 的坑：截图全黑、探针全落空，但紧接着
            # 用途选择页就渲染出来了）。两道闸：URL 必须已离开 /onboarding，且要连续
            # 若干轮都认不出 onboarding，才认定收尾。
            if progressed and not onboarding_visible(page):
                if _still_on_onboarding_url(page):
                    blank_rounds += 1
                    if blank_rounds * step < _BLANK_TRANSITION_MS:
                        log(f"页面切换中… {blank_rounds * step // 1000}s")
                        page.wait_for_timeout(step)
                        waited += step
                        continue
                log("已离开 onboarding 页面。")
                screenshot(page, "after_create_account.png")
                log(f"当前地址：{page.url}")
                return True
            blank_rounds = 0

            current_url = page.url
        except Exception as exc:
            log(f"建号后续等待时页面不可用（{exc}）。")
            try:
                screenshot(page, "after_create_account.png")
            except Exception:
                pass
            return progressed

        log(f"等待建号完成… {waited // 1000}s url={current_url}")
        try:
            page.wait_for_timeout(step)
        except Exception as exc:
            log(f"建号后续等待失败（{exc}）。")
            return progressed
        waited += step

    screenshot(page, "after_create_account.png")
    try:
        log(f"建号后未在超时内进入主界面。当前地址：{page.url}")
    except Exception:
        pass
    # 关键步骤点过就大致成功，浏览器留给人工确认。
    return progressed
