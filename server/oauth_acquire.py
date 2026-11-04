"""账号级 OAuth 授权：读账号的 sessionKey + 代理 → 自动授权 → 令牌落库。

面板单个/批量授权、开放 API、注册后自动授权共用这一处逻辑。
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, Request

from claude_register import oauth
from server import db


class AccountNotFound(Exception):
    pass


def parse_org_uuid(body) -> str | None:
    """从请求体取可选的 org_uuid；不合法时抛 HTTPException(400)。"""
    if body is None or body == {}:
        return None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="请求体必须是对象")
    org = body.get("org_uuid")
    if org is None or org == "":
        return None
    if not isinstance(org, str) or not org.strip() or len(org) > 128:
        raise HTTPException(status_code=400, detail="org_uuid 必须是非空字符串")
    return org.strip()


async def read_org_uuid(request: Request) -> str | None:
    """读可选 JSON 请求体里的 org_uuid（空 body 视为不指定）。"""
    if not await request.body():
        return None
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="请求体必须是 JSON") from None
    return parse_org_uuid(body)


def iso_from_unix(ts: int) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def unix_from_iso(text: str) -> int:
    if not text:
        return 0
    try:
        return int(datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")
                   .replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        return 0


def list_orgs(conn, email: str, *, now: str, preferred: str | None = None,
              lister=None) -> dict:
    """列出账号可授权的组织（工作空间），见 oauth.list_organizations。

    preferred 不填时以账号上次授权的组织为偏好。返回值额外带 current（上次授权的组织）。
    """
    row = db.get_account(conn, email)
    if row is None:
        raise AccountNotFound(email)
    lister = lister or oauth.list_organizations
    current = row.get("org_uuid") or ""
    try:
        result = lister(row.get("session_key") or "", row.get("proxy") or None,
                        preferred=preferred or current or None)
    except oauth.OAuthError as exc:
        if exc.kind == "dead":
            db.update_account_check(conn, email, "dead", now)
        raise
    return {**result, "current": current}


def acquire(conn, email: str, *, now: str, authorize=None,
            expect_email: str | None = None, org_uuid: str | None = None,
            preferred_org: str | None = None) -> dict:
    """为账号获取 OAuth 令牌并写库；返回更新后的账号行。

    账号不存在抛 AccountNotFound；授权失败抛 oauth.OAuthError，
    其中 kind=dead 时顺带把检测结果记为失效。
    expect_email 非空时，令牌所属的 Claude 账号邮箱必须与之一致，否则不落库、
    抛 OAuthError（防止把别的账号的令牌写给 sub2api）。
    org_uuid 指定授权的组织（工作空间，必须存在）；不指定时优先 preferred_org，
    再退回账号上次授权的组织，都不在列表里则按默认规则（team 优先）。
    """
    row = db.get_account(conn, email)
    if row is None:
        raise AccountNotFound(email)
    authorize = authorize or oauth.authorize_with_session_key
    try:
        kwargs = {}
        if org_uuid:
            kwargs["org_uuid"] = org_uuid
        preferred = preferred_org or row.get("org_uuid") or ""
        if preferred and not org_uuid:
            kwargs["preferred_org"] = preferred
        tokens = authorize(row.get("session_key") or "", row.get("proxy") or None, **kwargs)
    except oauth.OAuthError as exc:
        if exc.kind == "dead":
            db.update_account_check(conn, email, "dead", now)
        raise
    got = (tokens.email_address or "").strip().lower()
    want = (expect_email or "").strip().lower()
    if want and got and got != want:
        raise oauth.OAuthError(f"授权得到的是 {got} 的令牌，与目标账号 {want} 不一致，已放弃")
    db.update_account_oauth(conn, email, {
        "access_token": tokens.access_token,
        "refresh_token": tokens.refresh_token,
        "oauth_expires_at": iso_from_unix(tokens.expires_at),
        "oauth_scope": tokens.scope,
        "org_uuid": tokens.org_uuid,
        "org_name": tokens.org_name,
        "account_uuid": tokens.account_uuid,
        "oauth_at": now,
    })
    return db.get_account(conn, email)


def summary(row: dict) -> dict:
    """不含令牌明文的授权概况，用于接口回包与日志。"""
    return {
        "email": row.get("email") or "",
        "has_oauth": bool(row.get("access_token")),
        "oauth_expires_at": row.get("oauth_expires_at") or "",
        "oauth_at": row.get("oauth_at") or "",
        "org_uuid": row.get("org_uuid") or "",
        "org_name": row.get("org_name") or "",
        "account_uuid": row.get("account_uuid") or "",
    }
