"""选后缀、建邮箱。前缀由系统随机生成，用户只决定后缀。"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from claude_register.anymail import AnyMailClient, Mailbox
from claude_register.config import resolve_expires_hours
from claude_register.console import log
from claude_register.console import prompt as console_prompt


def utc_now_iso() -> str:
    """AnyMail 要的 ISO 8601 UTC 时间戳。"""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize(domain: str) -> str:
    return domain.strip().lstrip("@").strip(".").lower()


def choose_suffix(
    client: AnyMailClient,
    preferred: str | None = None,
    *,
    prompt=console_prompt,
) -> str:
    """决定用哪个后缀：--domain > ANYMAIL_DOMAIN > GET /api/domains 交互选择。"""
    if preferred:
        return _normalize(preferred)
    if client.domain:
        return client.domain

    domains = client.list_domains()
    if not domains:
        raise ValueError(
            "没有可用域名。请在 .env 设置 ANYMAIL_DOMAIN，"
            "或给 API Key 加上 domains:read scope。"
        )
    if len(domains) == 1:
        log(f"使用唯一域名：{domains[0]}")
        return domains[0]

    log("可用后缀：")
    for i, dom in enumerate(domains, start=1):
        log(f"  [{i}] {dom}")
    while True:
        raw = prompt("请选择后缀编号（直接回车=1）：")
        if not raw:
            return domains[0]
        if raw.isdigit() and 1 <= int(raw) <= len(domains):
            return domains[int(raw) - 1]
        log("输入无效，请重试。")


def create_for_suffix(client: AnyMailClient, domain: str) -> Mailbox:
    """按后缀建一个新邮箱，前缀随机（claude_<8位hex>）。"""
    expires_hours = resolve_expires_hours(os.getenv("ANYMAIL_EXPIRES_HOURS"))
    box = client.create_mailbox(
        local_part=None,  # 交给 anymail 生成 claude_<8位hex>
        domain=domain,
        expires_hours=expires_hours,
    )
    log(f"已创建邮箱：{box.email}")
    return box


def prepare_mailbox(
    client: AnyMailClient,
    *,
    email: str | None = None,
    domain: str | None = None,
    prompt=console_prompt,
) -> tuple[Mailbox, str]:
    """返回 (mailbox, since)。

    since 在任何账号写操作之前记录 —— 接码文档 §8.2：若用首次轮询时的 now()
    当 since，会漏掉「建邮箱完成 → 首次轮询」窗口内到达的邮件。
    这个顺序是本函数存在的理由，不要拆开。
    """
    since = utc_now_iso()

    if email:
        box = client.get_or_create_mailbox(email)
        log(f"使用指定邮箱：{box.email} (id={box.id or 'new'})")
        return box, since

    suffix = choose_suffix(client, domain, prompt=prompt)
    return create_for_suffix(client, suffix), since
