"""账号信息导出：可选字段 + 多种格式。面板导出与开放 API 共用。

默认字段与 text 格式和旧版「导出全部」逐字节一致（email / sessionkey / proxy /
mailUrl / mailKey 五行块），老用户的解析脚本不受影响。
"""
from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class ExportField:
    key: str      # API / JSON 里的字段名
    label: str    # text 格式里的行标签
    title: str    # 面板里显示的中文名
    secret: bool = False


FIELDS: tuple[ExportField, ...] = (
    ExportField("email", "email", "邮箱"),
    ExportField("session_key", "sessionkey", "sessionKey", secret=True),
    ExportField("proxy", "proxy", "代理", secret=True),
    ExportField("mail_base_url", "mailUrl", "邮箱服务地址"),
    ExportField("mail_key", "mailKey", "邮箱只读 Key", secret=True),
    ExportField("password", "password", "登录密码", secret=True),
    ExportField("display_name", "remark", "备注"),
    ExportField("domain", "domain", "域名"),
    ExportField("status", "status", "注册状态"),
    ExportField("check_status", "checkStatus", "检测结果"),
    ExportField("checked_at", "checkedAt", "检测时间"),
    ExportField("created_at", "createdAt", "创建时间"),
    ExportField("last_run_id", "runId", "注册任务 ID"),
)
FIELD_BY_KEY = {f.key: f for f in FIELDS}
DEFAULT_FIELDS: tuple[str, ...] = ("email", "session_key", "proxy", "mail_base_url", "mail_key")
FORMATS: tuple[str, ...] = ("text", "json", "csv", "line")
DEFAULT_LINE_SEP = "----"
MAX_SEP_LEN = 16


class ExportError(ValueError):
    """参数不合法；消息可直接回给调用方。"""


def parse_fields(raw) -> tuple[str, ...]:
    """'email,session_key' / ['email', ...] / None → 校验后的字段元组（保持调用方顺序、去重）。"""
    if raw is None:
        return DEFAULT_FIELDS
    items = raw.split(",") if isinstance(raw, str) else list(raw)
    keys: list[str] = []
    for item in items:
        key = str(item).strip()
        if not key:
            continue
        if key not in FIELD_BY_KEY:
            valid = ", ".join(FIELD_BY_KEY)
            raise ExportError(f"未知字段：{key}（可选：{valid}）")
        if key not in keys:
            keys.append(key)
    if not keys:
        raise ExportError("至少选择一个字段")
    return tuple(keys)


def parse_format(raw) -> str:
    fmt = (raw or "text").strip().lower()
    if fmt not in FORMATS:
        raise ExportError(f"未知格式：{fmt}（可选：{', '.join(FORMATS)}）")
    return fmt


def parse_sep(raw) -> str:
    sep = DEFAULT_LINE_SEP if raw is None else str(raw)
    if not sep or len(sep) > MAX_SEP_LEN or "\n" in sep or "\r" in sep:
        raise ExportError(f"分隔符需为 1–{MAX_SEP_LEN} 个字符且不能含换行")
    return sep


def pick(row: dict, fields) -> dict:
    """按字段挑出值；缺失/None 统一成空串（last_run_id 保留整数）。"""
    out = {}
    for key in fields:
        value = row.get(key)
        if value is None:
            value = ""
        elif key != "last_run_id":
            value = str(value)
        out[key] = value
    return out


def filter_rows(rows, *, emails=None, status=None, check_status=None) -> list[dict]:
    wanted = {e.strip().lower() for e in (emails or []) if e and e.strip()}
    out = []
    for row in rows:
        if wanted and str(row.get("email") or "").lower() not in wanted:
            continue
        if status and (row.get("status") or "") != status:
            continue
        if check_status and (row.get("check_status") or "") != check_status:
            continue
        out.append(row)
    return out


def _text_block(item: dict) -> str:
    return "\n".join(f"{FIELD_BY_KEY[k].label}：{v}" for k, v in item.items())


def render(rows, fields, fmt: str, *, sep: str = DEFAULT_LINE_SEP) -> tuple[str, str]:
    """返回 (正文, media_type)。"""
    items = [pick(r, fields) for r in rows]
    if fmt == "json":
        return json.dumps(items, ensure_ascii=False, indent=2) + "\n", "application/json"
    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerow(fields)
        for item in items:
            writer.writerow([item[k] for k in fields])
        # 带 BOM，Excel 直接打开中文不乱码
        return "\ufeff" + buf.getvalue(), "text/csv; charset=utf-8"
    if fmt == "line":
        body = "\n".join(sep.join(str(item[k]) for k in fields) for item in items)
        return (body + "\n") if body else "", "text/plain; charset=utf-8"
    body = "\n\n".join(_text_block(item) for item in items)
    return (body + "\n") if body else "", "text/plain; charset=utf-8"


FILE_EXT = {"text": "txt", "json": "json", "csv": "csv", "line": "txt"}


def describe() -> dict:
    """给面板/调用方展示的字段与格式说明。"""
    return {
        "fields": [
            {"key": f.key, "title": f.title, "label": f.label,
             "default": f.key in DEFAULT_FIELDS, "secret": f.secret}
            for f in FIELDS
        ],
        "formats": list(FORMATS),
        "default_fields": list(DEFAULT_FIELDS),
        "default_line_sep": DEFAULT_LINE_SEP,
    }
