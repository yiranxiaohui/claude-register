"""Playwright 操作。不依赖 anymail —— 验证码作为参数传入。"""

from __future__ import annotations

import contextvars
import os
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import unquote, urlsplit

from camoufox.sync_api import Camoufox
from playwright.sync_api import Page, expect

from claude_register.console import current_sink, log, prompt
from claude_register.socks_relay import SocksRelay

URL = "https://claude.ai/login"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"

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


@contextmanager
def browser_session(proxy: str | None = None):
    """启动 Camoufox（Firefox 系隐身浏览器）会话。

    headless="virtual" 自动包 Xvfb，适配无显示的容器，且比真 headless 更抗
    Cloudflare 检测；humanize 提供人性化光标移动；locale/geoip 让指纹统一
    （配了代理时 geoip 按代理出口 IP 匹配时区/地理指纹）。

    带认证的 SOCKS5 会先在本地拉起一个免认证中继（见 socks_relay），
    浏览器只连 127.0.0.1，凭据由中继负责递给上游。
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
    cm = Camoufox(
        headless="virtual",
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
        raise RuntimeError(
            f"启动 Camoufox 失败（{exc}）。请先运行 `uv run camoufox fetch` "
            "下载浏览器二进制，并确认已安装 Xvfb。"
        ) from exc
    log("已启动 Camoufox（headless=virtual）")
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
