"""AnyMail API 客户端：创建临时域名邮箱，供注册/登录脚本填入。

文档：D:\\Projects\\any-mail\\docs\\code-reception.md
认证：Authorization: Bearer ak_...
"""

from __future__ import annotations

import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path | None = None) -> None:
    """把 .env 读进 os.environ（已存在的环境变量不覆盖）。"""
    env_path = path or (ROOT / ".env")
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def normalize_base_url(base_url: str | None) -> str:
    raw = (base_url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    if not parsed.netloc:
        return ""
    return f"{parsed.scheme or 'https'}://{parsed.netloc}".rstrip("/")


@dataclass(frozen=True)
class Mailbox:
    id: str
    email: str
    expires_at: str | None = None
    tag: str | None = None


class AnyMailClient:
    """对接 self-hosted AnyMail（Cloudflare Workers）。"""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        domain: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        load_dotenv()
        self.base_url = normalize_base_url(
            base_url or os.getenv("ANYMAIL_BASE_URL") or os.getenv("ANY_MAIL_BASE_URL")
        )
        self.api_key = (
            api_key
            or os.getenv("ANYMAIL_API_KEY")
            or os.getenv("ANY_MAIL_API_KEY")
            or ""
        ).strip()
        self.domain = (
            domain
            or os.getenv("ANYMAIL_DOMAIN")
            or os.getenv("ANY_MAIL_DOMAIN")
            or ""
        ).strip().lstrip("@").strip(".").lower()
        self.timeout = timeout

        if not self.base_url:
            raise ValueError(
                "缺少 AnyMail 地址。请在 .env 设置 ANYMAIL_BASE_URL，"
                "例如 https://any-mail.xxx.workers.dev"
            )
        if not self.api_key:
            raise ValueError(
                "缺少 AnyMail API Key。请在 .env 设置 ANYMAIL_API_KEY（ak_ 开头），"
                "并确保包含 accounts:write（以及 domains:read，若未固定域名）。"
            )

    def _headers(self, *, content_type: bool = False) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        if content_type:
            headers["Content-Type"] = "application/json"
        return headers

    def list_domains(self) -> list[str]:
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.get(f"{self.base_url}/api/domains", headers=self._headers())
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"AnyMail 获取域名失败 {resp.status_code}: {resp.text[:300]}"
                )
            data = resp.json() if resp.content else {}

        items = data.get("domains") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []

        out: list[str] = []
        seen: set[str] = set()
        for item in items:
            if isinstance(item, dict):
                name = str(item.get("name") or item.get("domain") or "")
            else:
                name = str(item)
            dom = name.strip().lstrip("@").strip(".").lower()
            if dom and dom not in seen:
                seen.add(dom)
                out.append(dom)
        return out

    def list_accounts(
        self,
        *,
        search: str | None = None,
        provider: str | None = "domain",
        tag: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Mailbox]:
        """GET /api/accounts 列出已有邮箱。需要 scope accounts:read。"""
        params: dict[str, Any] = {
            "limit": max(1, min(int(limit), 100)),
            "offset": max(0, int(offset)),
        }
        if search:
            params["search"] = search
        if provider:
            params["provider"] = provider
        if tag is not None:
            params["tag"] = tag

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.get(
                f"{self.base_url}/api/accounts",
                headers=self._headers(),
                params=params,
            )
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"AnyMail 列出邮箱失败 {resp.status_code}: {resp.text[:300]}"
                )
            data = resp.json() if resp.content else {}

        items = data.get("accounts") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []

        out: list[Mailbox] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            email = str(item.get("email") or "").strip()
            if not email:
                continue
            out.append(
                Mailbox(
                    id=str(item.get("id") or ""),
                    email=email,
                    expires_at=item.get("expires_at"),
                    tag=item.get("tag"),
                )
            )
        return out

    def get_or_create_mailbox(
        self,
        email: str,
        *,
        expires_hours: float | None = 24.0,
        tag: str | None = "claude-register",
    ) -> Mailbox:
        """按完整邮箱地址选用已有账号；不存在则创建。"""
        target = email.strip().lower()
        if "@" not in target:
            raise ValueError(f"邮箱格式无效：{email}")

        existing = self.list_accounts(search=target, limit=100)
        for box in existing:
            if box.email.lower() == target:
                return box

        local, _, domain = target.partition("@")
        return self.create_mailbox(
            local_part=local,
            domain=domain,
            expires_hours=expires_hours,
            tag=tag,
        )

    def create_mailbox(
        self,
        *,
        local_part: str | None = None,
        domain: str | None = None,
        expires_hours: float | None = 24.0,
        tag: str | None = "claude-register",
    ) -> Mailbox:
        """POST /api/accounts 创建临时邮箱。"""
        dom = (domain or self.domain or "").strip().lstrip("@").strip(".").lower()
        if not dom:
            domains = self.list_domains()
            if not domains:
                raise ValueError(
                    "未配置 ANYMAIL_DOMAIN，且 GET /api/domains 无可用域名。"
                    "请固定域名，或给 API Key 加上 domains:read。"
                )
            dom = domains[0]

        local = (local_part or "").strip().lower() or f"claude_{secrets.token_hex(4)}"
        email = f"{local}@{dom}"

        body: dict[str, Any] = {"email": email}
        if tag:
            body["tag"] = tag
        if expires_hours is not None and expires_hours > 0:
            expires_at = datetime.now(timezone.utc) + timedelta(hours=expires_hours)
            body["expires_at"] = expires_at.isoformat().replace("+00:00", "Z")

        with httpx.Client(timeout=self.timeout) as client:
            # 409 表示地址已被占用，换一个本地部分重试几次
            last_error = ""
            for _ in range(5):
                resp = client.post(
                    f"{self.base_url}/api/accounts",
                    headers=self._headers(content_type=True),
                    json=body,
                )
                if resp.status_code == 409:
                    local = f"claude_{uuid.uuid4().hex[:10]}"
                    email = f"{local}@{dom}"
                    body["email"] = email
                    last_error = resp.text[:300]
                    continue
                if resp.status_code >= 400:
                    raise RuntimeError(
                        f"AnyMail 创建邮箱失败 {resp.status_code}: {resp.text[:300]}"
                    )
                data = resp.json()
                account = data.get("account") if isinstance(data, dict) else None
                if not isinstance(account, dict):
                    raise RuntimeError(f"AnyMail 返回异常：{data!r}")
                return Mailbox(
                    id=str(account.get("id") or ""),
                    email=str(account.get("email") or email),
                    expires_at=account.get("expires_at"),
                    tag=account.get("tag"),
                )
            raise RuntimeError(f"AnyMail 邮箱地址冲突，多次重试仍失败：{last_error}")

    def delete_mailbox(self, account_id: str) -> None:
        if not account_id:
            return
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.delete(
                f"{self.base_url}/api/accounts/{account_id}",
                headers=self._headers(),
            )
            if resp.status_code >= 400 and resp.status_code != 404:
                raise RuntimeError(
                    f"AnyMail 删除邮箱失败 {resp.status_code}: {resp.text[:300]}"
                )
