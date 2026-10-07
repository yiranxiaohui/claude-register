"""sub2api 对接：列出 sub2api 里的 Claude OAuth 账号，用本地 sessionKey 重新授权后把
新令牌写回 sub2api（POST /api/v1/admin/accounts/{id}/apply-oauth-credentials）。

- 鉴权用 sub2api「管理员 API Key」（请求头 x-api-key），不登录 sub2api 后台。
- apply-oauth-credentials 只替换令牌、保留并发/模型映射/分组等配置，并自动清除 error
  状态、失效旧令牌缓存——正是 sub2api 后台「重新授权」落库时用的接口。
- 账号对应关系：sub2api 账号的 extra.email_address，缺失时取名称里的邮箱，
  与本地账号邮箱匹配；授权得到的令牌所属邮箱必须与之一致才会推送。
- Key、令牌只在请求里出现，接口回包与日志一律不含。
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone

import httpx
from fastapi import Depends, FastAPI, HTTPException

from claude_register.console import log
from claude_register.oauth import OAuthError
from server import db, oauth_acquire

PLATFORM = "anthropic"
ACCOUNT_TYPE = "oauth"
PAGE_SIZE = 200
MAX_PAGES = 50
TIMEOUT = 20.0
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_DETAIL_MAX = 200
TRANSPORT = None  # 测试注入 httpx.MockTransport；生产为 None（真实网络）


class Sub2APIError(Exception):
    """调用 sub2api 失败；消息可直接展示（不含 Key / 令牌）。"""


def normalize_base_url(raw: str) -> str:
    url = (raw or "").strip().rstrip("/")
    if url.endswith("/api/v1"):
        url = url[: -len("/api/v1")]
    return url


class Sub2APIClient:
    def __init__(self, base_url: str, admin_key: str, *, transport=None, timeout=TIMEOUT):
        self.base_url = normalize_base_url(base_url)
        if not self.base_url or not admin_key:
            raise Sub2APIError("尚未配置 sub2api 地址或管理员 API Key（系统设置 → sub2api 对接）")
        if not self.base_url.startswith(("http://", "https://")):
            raise Sub2APIError("sub2api 地址必须以 http:// 或 https:// 开头")
        self._client = httpx.Client(
            base_url=self.base_url + "/api/v1/admin",
            headers={"x-api-key": admin_key, "Accept": "application/json"},
            timeout=timeout,
            transport=transport if transport is not None else TRANSPORT,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Sub2APIClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _request(self, method: str, path: str, **kw):
        try:
            resp = self._client.request(method, path, **kw)
        except httpx.HTTPError as exc:
            raise Sub2APIError(f"连接 sub2api 失败：{type(exc).__name__}") from None
        try:
            payload = resp.json()
        except ValueError:
            payload = None
        if resp.status_code == 401:
            raise Sub2APIError("sub2api 管理员 API Key 无效（HTTP 401）")
        if not 200 <= resp.status_code < 300:
            msg = payload.get("message") if isinstance(payload, dict) else None
            text = " ".join(str(msg or resp.text or "").split())[:_DETAIL_MAX]
            raise Sub2APIError(f"sub2api 返回 HTTP {resp.status_code}：{text}".rstrip("："))
        if not isinstance(payload, dict) or "data" not in payload:
            raise Sub2APIError("sub2api 响应格式不正确")
        return payload["data"]

    def list_claude_oauth_accounts(self) -> list[dict]:
        items: list[dict] = []
        for page in range(1, MAX_PAGES + 1):
            data = self._request("GET", "/accounts", params={
                "platform": PLATFORM, "type": ACCOUNT_TYPE,
                "page": page, "page_size": PAGE_SIZE,
                "sort_by": "name", "sort_order": "asc",
            })
            batch = data.get("items") if isinstance(data, dict) else None
            if not isinstance(batch, list):
                raise Sub2APIError("sub2api 账号列表格式不正确")
            items.extend(a for a in batch if isinstance(a, dict))
            pages = int(data.get("pages") or 1) if isinstance(data, dict) else 1
            if page >= pages or not batch:
                break
        return items

    def get_account(self, account_id: int) -> dict:
        data = self._request("GET", f"/accounts/{int(account_id)}")
        if not isinstance(data, dict):
            raise Sub2APIError("sub2api 账号详情格式不正确")
        return data

    def apply_oauth(self, account_id: int, row: dict) -> dict:
        data = self._request(
            "POST", f"/accounts/{int(account_id)}/apply-oauth-credentials",
            json=apply_payload(row),
        )
        return data if isinstance(data, dict) else {}


def apply_payload(row: dict) -> dict:
    """本地账号行（已有新令牌）→ apply-oauth-credentials 请求体。与 sub2api 刷新令牌时
    写入的字段一致（数值用字符串）。"""
    expires_at = oauth_acquire.unix_from_iso(str(row.get("oauth_expires_at") or ""))
    obtained = oauth_acquire.unix_from_iso(str(row.get("oauth_at") or ""))
    credentials = {
        "access_token": str(row["access_token"]),
        "token_type": "Bearer",
        "scope": str(row.get("oauth_scope") or ""),
    }
    if row.get("refresh_token"):
        credentials["refresh_token"] = str(row["refresh_token"])
    if expires_at:
        credentials["expires_at"] = str(expires_at)
        if obtained and expires_at > obtained:
            credentials["expires_in"] = str(expires_at - obtained)
    extra = {k: str(row[k]) for k in ("org_uuid", "account_uuid") if row.get(k)}
    if row.get("email"):
        extra["email_address"] = str(row["email"]).lower()
    return {"type": ACCOUNT_TYPE, "credentials": credentials, "extra": extra}


def account_email(acct: dict) -> str:
    """sub2api 账号对应的 Claude 邮箱：优先 extra.email_address，其次名称里的邮箱。"""
    extra = acct.get("extra") if isinstance(acct.get("extra"), dict) else {}
    value = extra.get("email_address")
    if isinstance(value, str) and "@" in value:
        return value.strip().lower()
    m = _EMAIL_RE.search(str(acct.get("name") or ""))
    return m.group(0).lower() if m else ""


def _iso(value) -> str:
    try:
        ts = int(str(value).strip())
    except (TypeError, ValueError):
        return ""
    if ts <= 0:
        return ""
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def summarize(acct: dict, local: dict | None) -> dict:
    """sub2api 账号 + 本地匹配情况；不含任何令牌 / sessionKey。"""
    creds = acct.get("credentials") if isinstance(acct.get("credentials"), dict) else {}
    status = acct.get("credentials_status") if isinstance(
        acct.get("credentials_status"), dict) else {}
    return {
        "id": acct.get("id"),
        "name": acct.get("name") or "",
        "email": account_email(acct),
        "status": acct.get("status") or "",
        "error_message": acct.get("error_message") or "",
        "schedulable": bool(acct.get("schedulable")),
        "token_expires_at": _iso(creds.get("expires_at")),
        "has_refresh_token": bool(status.get("has_refresh_token")),
        "updated_at": acct.get("updated_at") or "",
        "local": None if local is None else {
            "email": local["email"],
            "has_session_key": bool(local.get("session_key")),
            "check_status": local.get("check_status") or "",
            "checked_at": local.get("checked_at") or "",
            "oauth_at": local.get("oauth_at") or "",
        },
    }


def register_sub2api_routes(app: FastAPI, state, *, require_auth) -> None:
    def client() -> Sub2APIClient:
        cfg = state.config()
        try:
            return Sub2APIClient(cfg.sub2api_base_url, cfg.sub2api_admin_key)
        except Sub2APIError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    def local_by_email() -> dict[str, dict]:
        return {str(r["email"]).lower(): r for r in db.list_accounts(state.conn) if r.get("email")}

    @app.get("/api/sub2api/accounts")
    async def sub2api_accounts(_=Depends(require_auth)):
        """sub2api 里的 Claude OAuth 账号，附带本地账号匹配情况。"""
        def work():
            with client() as c:
                return c.list_claude_oauth_accounts()

        try:
            items = await asyncio.to_thread(work)
        except Sub2APIError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from None
        local = local_by_email()
        out = [summarize(a, local.get(account_email(a))) for a in items]
        return {"base_url": normalize_base_url(state.config().sub2api_base_url),
                "accounts": out}

    @app.post("/api/sub2api/accounts/{account_id}/reauth")
    async def sub2api_reauth(account_id: int, _=Depends(require_auth)):
        """用对应本地账号的 sessionKey 重新授权，并把新令牌写回该 sub2api 账号。"""
        c = client()
        try:
            try:
                acct = await asyncio.to_thread(c.get_account, account_id)
            except Sub2APIError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from None
            if acct.get("platform") != PLATFORM or acct.get("type") != ACCOUNT_TYPE:
                raise HTTPException(status_code=400, detail="该 sub2api 账号不是 Claude OAuth 账号")
            email = account_email(acct)
            if not email:
                raise HTTPException(status_code=400, detail="无法从该 sub2api 账号识别邮箱")
            row = local_by_email().get(email)
            if row is None:
                raise HTTPException(status_code=404, detail=f"本地没有账号 {email}")
            if not row.get("session_key"):
                raise HTTPException(status_code=400, detail=f"本地账号 {email} 没有 sessionKey")
            try:
                row = await asyncio.to_thread(
                    oauth_acquire.acquire, state.conn, row["email"],
                    now=state.now_fn(), expect_email=email,
                )
            except OAuthError as exc:
                log(f"sub2api 重新授权失败：#{account_id} {email}：{exc}")
                raise HTTPException(status_code=422, detail=f"OAuth 授权失败：{exc}") from None
            try:
                updated = await asyncio.to_thread(c.apply_oauth, account_id, row)
            except Sub2APIError as exc:
                log(f"sub2api 推送失败：#{account_id} {email}：{exc}")
                raise HTTPException(
                    status_code=502, detail=f"已重新授权，但写回 sub2api 失败：{exc}",
                ) from None
        finally:
            c.close()
        log(f"已重新授权并写回 sub2api：#{account_id} {email}"
            f"（过期 {row.get('oauth_expires_at') or '未知'}）")
        return {
            "ok": True,
            "email": email,
            "oauth_expires_at": row.get("oauth_expires_at") or "",
            "account": summarize({**acct, **updated}, row),
        }
