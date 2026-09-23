"""claude.ai session key 存活检测：拿 sessionKey cookie 请求鉴权接口，判三态。

纯函数，不碰数据库/FastAPI。所有异常收敛为 ("error", 原因)，绝不上抛。
"""
from __future__ import annotations

from curl_cffi import requests as cffi_requests

from claude_register.browser import mask_proxy, needs_relay, normalize_proxy_url, parse_proxy
from claude_register.socks_relay import SocksRelay

ORG_URL = "https://claude.ai/api/organizations"
# 不自带 User-Agent：UA 由 impersonate 按所模拟的浏览器给出，手写一个会跟
# TLS/HTTP2 指纹对不上，反而更像机器人。
_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
}
# 模拟的浏览器指纹。实测 httpx 不论直连还是走代理都被 Cloudflare 盾
# （403 + cf-mitigated: challenge）拦下——拦的是 Python TLS 指纹，不是 IP；
# curl_cffi 模拟浏览器后同一出口直接拿到 200。
IMPERSONATE = "chrome"


class _ImpersonatingClient:
    """curl_cffi 浏览器指纹客户端；带凭据的 SOCKS5 走本地中继。

    为什么带凭据的 SOCKS5 要绕中继：实测 curl 直连这类机场上游，SOCKS 握手
    成功但 TLS ClientHello 一发出去就被掐断；经 SocksRelay 转发则正常。中继还
    自带上游并发闸与握手重试（上游有 ~4 条并发上限），跟注册时浏览器走的是
    同一条路，检测结果也就跟真实使用一致。

    SOCKS 一律用 socks5h（域名交给上游解析）：本地 DNS 可能是 Clash fake-ip
    （198.18.x.x），本地解析出的虚拟地址拿去 CONNECT 上游只会被拒。
    """

    def __init__(self, proxy: str | None, *, timeout: float = 15.0):
        self._relay: SocksRelay | None = None
        target = proxy
        if proxy and needs_relay(parse_proxy(proxy)):
            self._relay = SocksRelay(proxy).start()
            target = self._relay.local_url
        if target and target.startswith("socks5://"):
            target = "socks5h://" + target[len("socks5://"):]
        self.proxy = target  # 实际交给 curl 的代理（经中继时是免认证本地口）
        try:
            self._session = cffi_requests.Session(
                impersonate=IMPERSONATE,
                proxy=target,
                timeout=timeout,
                allow_redirects=True,
            )
        except Exception:
            self._stop_relay()
            raise

    @property
    def uses_relay(self) -> bool:
        return self._relay is not None

    def get(self, url, *, headers=None, cookies=None, timeout=None):
        return self._session.get(url, headers=headers, cookies=cookies, timeout=timeout)

    def close(self) -> None:
        try:
            self._session.close()
        finally:
            self._stop_relay()

    def _stop_relay(self) -> None:
        if self._relay is not None:
            self._relay.stop()
            self._relay = None

    def __enter__(self) -> _ImpersonatingClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _default_client(proxy: str | None) -> _ImpersonatingClient:
    return _ImpersonatingClient(proxy)


def _looks_like_shield(resp) -> bool:
    """Cloudflare 盾：带 cf-mitigated 头，或响应体不是 JSON（HTML 挑战页）。"""
    if "cf-mitigated" in resp.headers:
        return True
    ctype = resp.headers.get("content-type", "")
    if "html" in ctype.lower():
        return True
    try:
        resp.json()
        return False
    except Exception:  # noqa: BLE001
        return True


def _looks_like_org_payload(resp) -> bool:
    """确认 200 真的是组织 API JSON，而不是代理返回的伪成功页面。"""
    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001
        return False
    if isinstance(payload, list):
        return all(isinstance(item, dict) for item in payload)
    if isinstance(payload, dict):
        items = payload.get("organizations")
        return isinstance(items, list) and all(isinstance(item, dict) for item in items)
    return False


def check_session(
    session_key: str,
    proxy: str | None = None,
    *,
    timeout: float = 15.0,
    client_factory=None,
) -> tuple[str, str]:
    if not session_key:
        return ("error", "无 sessionKey")
    try:
        proxy_url = normalize_proxy_url(proxy)
    except Exception:  # noqa: BLE001
        return ("error", f"代理无效：{mask_proxy(proxy or '')}")

    factory = client_factory or _default_client
    try:
        client = factory(proxy_url)
    except Exception as exc:  # noqa: BLE001
        # socks5 缺 socksio 会在建 client 时报错
        return ("error", f"发起请求失败：{exc}")

    try:
        with client:
            resp = client.get(
                ORG_URL,
                headers=_HEADERS,
                cookies={"sessionKey": session_key},
                timeout=timeout,
            )
    except Exception as exc:  # noqa: BLE001
        return ("error", f"请求失败：{type(exc).__name__}")

    if resp.status_code == 200 and not _looks_like_shield(resp):
        if _looks_like_org_payload(resp):
            return ("alive", "有效")
        return ("error", "响应不是有效的组织列表")
    if resp.status_code in (401, 403):
        if _looks_like_shield(resp):
            return ("blocked", "Cloudflare 盾拦截，无法判定")
        return ("dead", f"已失效（HTTP {resp.status_code}）")
    return ("error", f"未知响应（HTTP {resp.status_code}）")
