"""批量导入 sessionKey（sk）：解析文本 → 可选检测/取邮箱 → 入库。

每行一个账号，支持：
  sk-ant-sid01-…                      只有 sk，邮箱靠检测时向 claude.ai 查询
  email----sk / email:sk / email,sk   带邮箱（分隔符不限，sk 与邮箱按正则识别）
  sk----email                         顺序不限
空行与 # 开头的注释行忽略；同一批里重复的 sk 只导入一次。
"""
from __future__ import annotations

import hashlib
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from server import db

SK_RE = re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PLACEHOLDER_DOMAIN = "sk-import.local"
MAX_LINES = 500
# 代理上游常有 ~4 条并发上限（见 session_check），检测并发与之对齐
CHECK_WORKERS = 4


@dataclass
class ParsedLine:
    line: int
    session_key: str
    email: str = ""


def parse(text: str) -> tuple[list[ParsedLine], list[dict]]:
    """返回 (可导入条目, 解析失败条目)。"""
    items: list[ParsedLine] = []
    errors: list[dict] = []
    seen: set[str] = set()
    for no, raw in enumerate((text or "").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        sk = SK_RE.search(line)
        if not sk:
            errors.append({"line": no, "result": "invalid", "detail": "未找到 sk-ant- 开头的 sessionKey"})
            continue
        key = sk.group(0)
        if key in seen:
            errors.append({"line": no, "result": "duplicate", "detail": "与前面的行重复"})
            continue
        seen.add(key)
        rest = line[: sk.start()] + " " + line[sk.end():]
        email = EMAIL_RE.search(rest)
        items.append(ParsedLine(no, key, email.group(0).lower() if email else ""))
    return items, errors


def placeholder_email(session_key: str) -> str:
    """查不到邮箱时的占位标识：同一个 sk 总是得到同一个值，重复导入不会产生多条。"""
    digest = hashlib.sha256(session_key.encode()).hexdigest()[:12]
    return f"sk-{digest}@{PLACEHOLDER_DOMAIN}"


def mask_sk(session_key: str) -> str:
    return f"{session_key[:17]}…{session_key[-4:]}" if len(session_key) > 24 else "sk-…"


def run_import(
    conn,
    text: str,
    *,
    proxy: str,
    check: bool,
    skip_dead: bool,
    now: str,
    probe,
) -> dict:
    """执行导入。probe(session_key, proxy, want_email) -> (status, detail, email)。"""
    items, results = parse(text)
    if len(items) + len(results) > MAX_LINES:
        raise ValueError(f"单次最多导入 {MAX_LINES} 行")

    probed: dict[int, tuple[str, str, str]] = {}
    if check:
        def _probe(item: ParsedLine):
            return item.line, probe(item.session_key, proxy, not item.email)

        with ThreadPoolExecutor(max_workers=CHECK_WORKERS) as pool:
            for line, res in pool.map(_probe, items):
                probed[line] = res

    for item in items:
        status, detail, found_email = probed.get(item.line, ("", "", ""))
        base = {"line": item.line, "sk": mask_sk(item.session_key), "check_status": status or None}
        if check and skip_dead and status == "dead":
            results.append({**base, "email": item.email, "result": "skipped", "detail": detail})
            continue
        email = item.email or found_email or placeholder_email(item.session_key)
        existing = db.get_account(conn, email)
        if existing is None:
            db.upsert_account(
                conn, email, email.split("@", 1)[1], "", "", None, "success",
                session_key=item.session_key, proxy=proxy, created_at=now,
            )
            result = "created"
        else:
            fields = {"session_key": item.session_key}
            if proxy:
                fields["proxy"] = proxy
            db.update_account_fields(conn, email, fields)
            result = "updated"
        if status:
            db.update_account_check(conn, email, status, now)
        note = detail
        if not item.email and not found_email:
            note = (detail + "；" if detail else "") + "未取得邮箱，使用占位标识"
        results.append({**base, "email": email, "result": result, "detail": note})

    results.sort(key=lambda r: r["line"])
    summary = {k: sum(1 for r in results if r["result"] == k)
               for k in ("created", "updated", "skipped", "invalid", "duplicate")}
    return {"summary": summary, "results": results}
