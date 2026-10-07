"""用 claude.ai 的 sessionKey 自动完成 Claude OAuth 授权（PKCE），换取
access_token / refresh_token。

流程与 sub2api 的「Cookie 自动授权」（/api/v1/admin/accounts/cookie-auth）一致，
全程不开浏览器：

1. GET  https://claude.ai/api/organizations               （sessionKey Cookie）取组织 UUID
2. 本地生成 PKCE：code_verifier / code_challenge(S256) / state
3. POST https://claude.ai/v1/oauth/{org}/authorize        （sessionKey Cookie）拿授权码
4. POST https://platform.claude.com/v1/oauth/token        用授权码 + code_verifier 换令牌

授权使用完整 scope（不含浏览器授权页才有的 org:create_api_key，内部接口不支持）。
claude.ai 两步复用 session_check 的浏览器指纹客户端（同一条代理/中继），
否则会被 Cloudflare 盾拦下。令牌只放在返回值里，日志与异常信息一律不含令牌。
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import asdict, dataclass
from urllib.parse import parse_qs, urlsplit

from claude_register.browser import mask_proxy, normalize_proxy_url
from claude_register.session_check import _default_client, _looks_like_shield

CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
REDIRECT_URI = "https://platform.claude.com/oauth/code/callback"
TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
CLAUDE_BASE = "https://claude.ai"
ORG_URL = CLAUDE_BASE + "/api/organizations"
# 完整 OAuth scope（内部授权接口不支持 org:create_api_key）
SCOPE_FULL = (
    "user:profile user:inference user:sessions:claude_code "
    "user:mcp_servers user:file_upload"
)
# 与 Claude Code CLI 一致：令牌接口按 axios 客户端识别
TOKEN_USER_AGENT = "axios/1.13.6"
DEFAULT_TIMEOUT = 30.0
_DETAIL_MAX = 200


class OAuthError(Exception):
    """授权失败。kind：dead（sessionKey 已失效）/ blocked（被 Cloudflare 拦截）/ error。"""

    def __init__(self, message: str, *, kind: str = "error"):
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class OAuthTokens:
    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int
    expires_at: int  # unix 秒
    scope: str
    org_uuid: str
    account_uuid: str
    email_address: str

    def to_dict(self) -> dict:
        return asdict(self)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def generate_pkce() -> tuple[str, str, str]:
    """返回 (code_verifier, code_challenge, state)。verifier/state 各 32 字节随机数。"""
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    state = _b64url(secrets.token_bytes(32))
    return verifier, challenge, state


def _short(resp) -> str:
    try:
        text = resp.text or ""
    except Exception:  # noqa: BLE001
        text = ""
    text = " ".join(text.split())
    return text[:_DETAIL_MAX] + ("…" if len(text) > _DETAIL_MAX else "")


def _json(resp):
    try:
        return resp.json()
    except Exception:  # noqa: BLE001
        return None


def _http_error(step: str, resp) -> OAuthError:
    if resp.status_code in (401, 403):
        if _looks_like_shield(resp):
            return OAuthError(f"{step}：被 Cloudflare 盾拦截（HTTP {resp.status_code}）",
                              kind="blocked")
        if step.startswith("获取组织"):
            return OAuthError(f"{step}：sessionKey 已失效（HTTP {resp.status_code}）",
                              kind="dead")
    return OAuthError(f"{step}：HTTP {resp.status_code} {_short(resp)}".rstrip())


def pick_org_uuid(payload) -> str:
    """多个组织时优先 raven_type == "team"，否则取第一个（与 sub2api 相同）。"""
    if isinstance(payload, dict):
        payload = payload.get("organizations")
    if not isinstance(payload, list):
        raise OAuthError("获取组织：响应不是组织列表")
    orgs = [o for o in payload if isinstance(o, dict) and o.get("uuid")]
    if not orgs:
        raise OAuthError("获取组织：账号下没有组织")
    for org in orgs:
        if org.get("raven_type") == "team":
            return str(org["uuid"])
    return str(orgs[0]["uuid"])


def _claude_headers() -> dict:
    return {
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Origin": CLAUDE_BASE,
        "Referer": CLAUDE_BASE + "/new",
    }


def _token_headers() -> dict:
    return {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "User-Agent": TOKEN_USER_AGENT,
    }


def _authorization_code(client, session_key, org_uuid, challenge, state, timeout) -> tuple[str, str]:
    body = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "organization_uuid": org_uuid,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE_FULL,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    resp = client.post(
        f"{CLAUDE_BASE}/v1/oauth/{org_uuid}/authorize",
        json=body,
        headers={**_claude_headers(), "Content-Type": "application/json"},
        cookies={"sessionKey": session_key},
        timeout=timeout,
    )
    if not 200 <= resp.status_code < 300:
        raise _http_error("获取授权码", resp)
    payload = _json(resp)
    redirect = payload.get("redirect_uri") if isinstance(payload, dict) else None
    if not isinstance(redirect, str) or not redirect:
        raise OAuthError("获取授权码：响应里没有 redirect_uri")
    query = parse_qs(urlsplit(redirect).query)
    code = (query.get("code") or [""])[0]
    if not code:
        raise OAuthError("获取授权码：redirect_uri 里没有 code")
    return code, (query.get("state") or [""])[0]


def _parse_tokens(payload, *, org_uuid: str, now: float) -> OAuthTokens:
    if not isinstance(payload, dict) or not payload.get("access_token"):
        raise OAuthError("换取令牌：响应里没有 access_token")
    try:
        expires_in = int(payload.get("expires_in") or 0)
    except (TypeError, ValueError):
        expires_in = 0
    org = payload.get("organization") if isinstance(payload.get("organization"), dict) else {}
    acct = payload.get("account") if isinstance(payload.get("account"), dict) else {}
    return OAuthTokens(
        access_token=str(payload["access_token"]),
        refresh_token=str(payload.get("refresh_token") or ""),
        token_type=str(payload.get("token_type") or "Bearer"),
        expires_in=expires_in,
        expires_at=int(now) + expires_in,
        scope=str(payload.get("scope") or SCOPE_FULL),
        org_uuid=str(org.get("uuid") or org_uuid or ""),
        account_uuid=str(acct.get("uuid") or ""),
        email_address=str(acct.get("email_address") or "").strip().lower(),
    )


def _exchange(client, code, verifier, state, timeout) -> dict:
    body = {
        "code": code,
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": verifier,
    }
    if state:
        body["state"] = state
    resp = client.post(TOKEN_URL, json=body, headers=_token_headers(), timeout=timeout)
    if not 200 <= resp.status_code < 300:
        raise _http_error("换取令牌", resp)
    return _json(resp)


def authorize_with_session_key(
    session_key: str,
    proxy: str | None = None,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    client_factory=None,
    now_fn=time.time,
) -> OAuthTokens:
    """用 sessionKey 走完整 OAuth 授权，返回令牌；失败抛 OAuthError（不含任何凭据）。"""
    if not session_key:
        raise OAuthError("该账号没有 sessionKey")
    try:
        proxy_url = normalize_proxy_url(proxy)
    except Exception:  # noqa: BLE001
        raise OAuthError(f"代理无效：{mask_proxy(proxy or '')}") from None

    factory = client_factory or _default_client
    try:
        client = factory(proxy_url)
    except Exception as exc:  # noqa: BLE001
        raise OAuthError(f"发起请求失败：{type(exc).__name__}") from None

    try:
        with client:
            resp = client.get(
                ORG_URL,
                headers={"Accept": "*/*", "Accept-Language": "en-US,en;q=0.9"},
                cookies={"sessionKey": session_key},
                timeout=timeout,
            )
            if resp.status_code != 200 or _looks_like_shield(resp):
                if resp.status_code == 200:
                    raise OAuthError("获取组织：被 Cloudflare 盾拦截", kind="blocked")
                raise _http_error("获取组织", resp)
            org_uuid = pick_org_uuid(_json(resp))
            verifier, challenge, state = generate_pkce()
            code, code_state = _authorization_code(
                client, session_key, org_uuid, challenge, state, timeout,
            )
            payload = _exchange(client, code, verifier, code_state or state, timeout)
    except OAuthError:
        raise
    except Exception as exc:  # noqa: BLE001 — 网络/代理异常，类型名足够定位且不泄露凭据
        raise OAuthError(f"请求失败：{type(exc).__name__}") from None
    return _parse_tokens(payload, org_uuid=org_uuid, now=now_fn())
