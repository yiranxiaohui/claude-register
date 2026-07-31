"""claude.ai session key 存活检测：拿 sessionKey cookie 请求鉴权接口，判三态。

纯函数，不碰数据库/FastAPI。所有异常收敛为 ("error", 原因)，绝不上抛。
"""
from __future__ import annotations

import httpx

from claude_register.browser import normalize_proxy_url

ORG_URL = "https://claude.ai/api/organizations"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
}


def _default_client(proxy: str | None) -> httpx.Client:
    kwargs = {"timeout": 15.0, "follow_redirects": True}
    if proxy:
        kwargs["proxy"] = proxy
    return httpx.Client(**kwargs)


def _looks_like_shield(resp: httpx.Response) -> bool:
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
    except Exception as exc:  # noqa: BLE001
        return ("error", f"代理无效：{exc}")

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
        return ("alive", "有效")
    if resp.status_code in (401, 403):
        if _looks_like_shield(resp):
            return ("error", "疑似 Cloudflare 盾拦截")
        return ("dead", f"已失效（HTTP {resp.status_code}）")
    return ("error", f"未知响应（HTTP {resp.status_code}）")
