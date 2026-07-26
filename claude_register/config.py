"""环境变量解析。集中在这里，避免各处散落 os.getenv + 兜底逻辑。"""

from __future__ import annotations

import re

from claude_register.console import log

# 主正则用捕获组定位真正的码，避开邮件里的日期数字（接码文档 §8.4）
DEFAULT_CODE_REGEX = r"code[^\d]*(\d{6})"
# 兜底正则：要求 6 位数字前面不是 #，避开邮件 HTML 里的 CSS 颜色值
# （实测坑：#000000 / #737163 / #262624 都会被裸 \b\d{6}\b 命中）
FALLBACK_CODE_REGEX = r"(?<![#0-9A-Fa-f])\b(\d{6})\b"

# 魔术链接：https://claude.ai/magic-link#<32位hex>:<base64(邮箱)>
MAGIC_LINK_REGEX = r"https://claude\.ai/magic-link#[A-Za-z0-9+/=:._-]+"

DEFAULT_EXPIRES_HOURS = 24.0


def resolve_expires_hours(
    raw: str | None,
    *,
    default: float = DEFAULT_EXPIRES_HOURS,
) -> float | None:
    """解析邮箱有效期小时数。

    空 → default；正数 → 该值；<=0 → None（永久，不传 expires_at）。
    非数字 → default，并打印提示。
    """
    text = (raw or "").strip()
    if not text:
        return default
    try:
        hours = float(text)
    except ValueError:
        log(f"ANYMAIL_EXPIRES_HOURS={text!r} 不是数字，改用默认 {default} 小时。")
        return default
    return hours if hours > 0 else None


def resolve_code_regex(raw: str | None) -> str:
    """解析接码正则。语法错时退回默认值并提示。"""
    text = (raw or "").strip()
    if not text:
        return DEFAULT_CODE_REGEX
    try:
        re.compile(text)
    except re.error as exc:
        log(f"ANYMAIL_CODE_REGEX 语法错（{exc}），改用默认正则。")
        return DEFAULT_CODE_REGEX
    return text
