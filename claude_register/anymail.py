"""AnyMail API 客户端：创建临时域名邮箱，供注册/登录脚本填入。

文档：D:\\Projects\\any-mail\\docs\\code-reception.md
认证：Authorization: Bearer ak_...
"""

from __future__ import annotations

import base64
import os
import re
import secrets
import string
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from claude_register.config import (
    FALLBACK_CODE_REGEX,
    MAGIC_LINK_REGEX,
    resolve_code_regex,
)
from claude_register.console import log

ROOT = Path(__file__).resolve().parent.parent


def random_local(length: int = 10) -> str:
    """纯小写字母随机邮箱名——无固定前缀、不含数字，降低命中注册风控的概率。"""
    return "".join(secrets.choice(string.ascii_lowercase) for _ in range(length))


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


@dataclass(frozen=True)
class ChildKey:
    """按次派生的受限子 key(仅 emails:read + 锁定单邮箱)。明文只在创建响应可得。"""

    id: str
    plaintext: str


# 致命错误：不会因为重试而变好（正则语法错 / key 失效 / scope 不足）
FATAL_STATUSES = frozenset({400, 401, 403})


class AnyMailAccessError(RuntimeError):
    """API Key 无效或无读信权限；调用方可换一把凭据后立即重试。"""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        if status_code == 401:
            hint = "API Key 不存在、已被撤销或已失效"
        else:
            hint = "API Key 缺少 emails:read scope，或无权读取该邮箱"
        super().__init__(f"AnyMail 接码失败 {status_code}: {detail}\n{hint}。")


def extract_code(email: dict[str, Any], regex: str) -> str | None:
    """在 subject / text_body / html_body 里找验证码。

    有捕获组返回第 1 组，否则返回整段匹配（与 AnyMail 服务端行为一致）。
    """
    try:
        pattern = re.compile(regex, re.IGNORECASE)
    except re.error:
        return None
    for field in ("subject", "text_body", "html_body"):
        value = email.get(field) or ""
        match = pattern.search(str(value))
        if match:
            return match.group(1) if match.groups() else match.group(0)
    return None


def magic_link_recipient(link: str) -> str | None:
    """从魔术链接尾部的 base64 解出收件邮箱；解不出返回 None。"""
    frag = link.partition("#")[2]
    b64 = frag.partition(":")[2]
    if not b64:
        return None
    try:
        pad = "=" * (-len(b64) % 4)
        email = base64.b64decode(b64 + pad).decode("utf-8", "replace")
    except Exception:
        return None
    return email.strip().lower() if "@" in email else None


def extract_magic_link(email: dict[str, Any]) -> str | None:
    """从邮件里提取 Claude 魔术登录链接。

    实测：链接只在 html_body 里（text_body 为空），且是 HTML 转义过的，
    所以必须先 unescape 再匹配。
    """
    pattern = re.compile(MAGIC_LINK_REGEX)
    for field in ("text_body", "html_body", "subject"):
        raw = email.get(field) or ""
        match = pattern.search(unescape(str(raw)))
        if match:
            return match.group(0).rstrip("&")
    return None


class AnyMailClient:
    """对接 self-hosted AnyMail（Cloudflare Workers）。"""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        domain: str | None = None,
        code_regex: str | None = None,
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
        self.code_regex = (code_regex or "").strip()
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
        expires_hours: float | None = None,
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
        expires_hours: float | None = None,
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

        local = (local_part or "").strip().lower() or random_local()
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
                    local = random_local(12)
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

    def _fetch_latest(
        self,
        *,
        to: str,
        since: str,
        code_regex: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """单次 GET /api/emails/latest。致命状态码直接抛，其余交调用方退避。"""
        params = {
            "to": to,
            "since": since,
            "code_regex": code_regex,
            "limit": max(1, min(int(limit), 50)),
        }
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.get(
                f"{self.base_url}/api/emails/latest",
                headers=self._headers(),
                params=params,
            )
            if resp.status_code in (401, 403):
                raise AnyMailAccessError(resp.status_code, resp.text[:300])
            if resp.status_code in FATAL_STATUSES:
                raise RuntimeError(
                    f"AnyMail 接码失败 {resp.status_code}: {resp.text[:300]}\n"
                    "请确认 code_regex 语法正确。"
                )
            if resp.status_code >= 400:
                # 5xx：交给调用方指数退避
                raise httpx.HTTPStatusError(
                    f"{resp.status_code}: {resp.text[:200]}",
                    request=resp.request,
                    response=resp,
                )
            try:
                data = resp.json() if resp.content else {}
            except ValueError as exc:
                # 200 但响应体不是合法 JSON（比如网关返回的 HTML 错误页）：
                # 交给调用方按退避重试，不当作致命错误、更不能让异常逃出轮询循环。
                raise httpx.HTTPStatusError(
                    f"响应体不是合法 JSON: {exc}",
                    request=resp.request,
                    response=resp,
                ) from exc

        emails = data.get("emails") if isinstance(data, dict) else None
        return [e for e in emails if isinstance(e, dict)] if isinstance(emails, list) else []

    def check_email_access(self, *, to: str) -> None:
        """只读探测当前 Key 能否读取指定邮箱，不等待或消费邮件。"""
        since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._fetch_latest(to=to, since=since, code_regex="", limit=1)

    def poll_code(
        self,
        *,
        to: str,
        since: str,
        code_regex: str | None = None,
        fallback_regex: str | None = None,
        timeout: float = 120.0,
        interval: float = 3.0,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> str | None:
        """轮询直到取到验证码；超时返回 None（调用方走降级路径）。

        两级匹配在单次响应内完成：先用服务端提取的 code，没有再用兜底正则
        匹配同一批邮件的正文——避免每轮翻倍请求。
        """
        primary = (
            code_regex
            or self.code_regex
            or resolve_code_regex(os.getenv("ANYMAIL_CODE_REGEX"))
        )
        fallback = fallback_regex or FALLBACK_CODE_REGEX
        deadline = monotonic() + timeout
        backoff = 1.0

        log(f"开始接码：{to}（超时 {timeout:.0f}s，每 {interval:.0f}s 一次）")
        while monotonic() < deadline:
            try:
                emails = self._fetch_latest(
                    to=to, since=since, code_regex=primary
                )
            except httpx.HTTPError as exc:
                log(f"接码请求失败（{exc}），{backoff:.0f}s 后重试。")
                sleep(backoff)
                backoff = min(backoff * 2, 4.0)
                continue
            backoff = 1.0

            for email in emails:
                code = email.get("code")
                if code:
                    return str(code)
            for email in emails:
                code = extract_code(email, fallback)
                if code:
                    log("服务端未提取到码，已用兜底正则命中。")
                    return code

            sleep(interval)

        return None

    def poll_magic_link(
        self,
        *,
        to: str,
        since: str,
        timeout: float = 120.0,
        interval: float = 3.0,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> str | None:
        """轮询直到拿到魔术登录链接；超时返回 None。

        只接受 base64 尾巴解出来等于 to 的链接——避免抓错别的邮箱的邮件。
        """
        target = to.strip().lower()
        deadline = monotonic() + timeout
        backoff = 1.0

        log(f"开始等待登录链接：{to}（超时 {timeout:.0f}s，每 {interval:.0f}s 一次）")
        while monotonic() < deadline:
            try:
                emails = self._fetch_latest(to=to, since=since, code_regex="")
            except httpx.HTTPError as exc:
                log(f"查询邮件失败（{exc}），{backoff:.0f}s 后重试。")
                sleep(backoff)
                backoff = min(backoff * 2, 4.0)
                continue
            backoff = 1.0

            for email in emails:
                link = extract_magic_link(email)
                if not link:
                    continue
                who = magic_link_recipient(link)
                if who is None:
                    # AnyMail 的 to 是 LIKE 匹配，不是精确匹配，收件人校验解不出来时
                    # 不能当成"没问题"放行——宁可继续等，也不能拿错邮箱的链接去登录。
                    log("跳过收件人无法解析的链接（base64 尾巴解不出邮箱）。")
                    continue
                if who != target:
                    log(f"跳过收件人不匹配的链接（{who}）。")
                    continue
                return link

            sleep(interval)

        return None

    def create_child_key(
        self,
        *,
        email: str,
        expires_at: str | None,
        name_prefix: str = "claude-register",
    ) -> ChildKey | None:
        """POST /api/keys 派生仅 emails:read、锁定 email、随邮箱过期的子 key。

        任何失败(403 缺 keys:create / 400 子集越界 / 5xx / 网络错误 / 响应异形)
        都只警告并返回 None——派生失败不值得中断注册,调用方降级用父 key 轮询。
        """
        target = email.strip().lower()
        body: dict[str, Any] = {
            "name": f"{name_prefix} {target}",
            "scopes": ["emails:read"],
            "provider": "domain",
            "address": target,
            "expires_at": expires_at,
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(
                    f"{self.base_url}/api/keys",
                    headers=self._headers(content_type=True),
                    json=body,
                )
        except Exception as exc:  # noqa: BLE001 — 任何失败都只降级，不中断注册
            log(f"派生子 key 请求失败({exc}),降级:轮询继续用主 key。")
            return None

        if resp.status_code >= 400:
            log(
                f"派生子 key 失败 {resp.status_code}: {resp.text[:200]}。"
                "降级:轮询继续用主 key,导出的 mailKey 将为空。"
                "(403 通常是主 key 缺 keys:create;400 可能是子集越界,"
                "如主 key 带有效期而邮箱永久。)"
            )
            return None

        try:
            data = resp.json() if resp.content else {}
        except ValueError:
            data = {}
        key = data.get("key") if isinstance(data, dict) else None
        key_id = str(key.get("id") or "") if isinstance(key, dict) else ""
        plaintext = str(data.get("plaintext") or "") if isinstance(data, dict) else ""
        if not key_id or not plaintext:
            safe_data = dict(data) if isinstance(data, dict) else data
            if isinstance(safe_data, dict) and safe_data.get("plaintext"):
                safe_data["plaintext"] = "ak_…redacted"
            log(f"派生子 key 响应异常:{safe_data!r},降级:轮询继续用主 key。")
            return None
        return ChildKey(id=key_id, plaintext=plaintext)

    def delete_key(self, key_id: str) -> None:
        """DELETE /api/keys/{id} 撤销子 key。404 视为已删;其余失败只警告。

        回收失败不影响主流程——子 key 本身带过期兜底。
        """
        if not key_id:
            return
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.delete(
                    f"{self.base_url}/api/keys/{key_id}",
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            log(f"撤销子 key 请求失败({exc}),忽略——子 key 会随过期自动失效。")
            return
        if resp.status_code >= 400 and resp.status_code != 404:
            log(
                f"撤销子 key 失败 {resp.status_code}: {resp.text[:200]},"
                "忽略——子 key 会随过期自动失效。"
            )

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
