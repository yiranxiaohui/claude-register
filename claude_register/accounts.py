"""注册成功后的账号落盘:email / password / sessionKey / proxy / mailKey。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from claude_register.console import log

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ACCOUNTS_JSONL = ROOT / "data" / "accounts.jsonl"


@dataclass
class AccountRecord:
    email: str
    password: str = ""
    sessionKey: str = ""
    proxy: str = ""
    display_name: str = ""
    mailbox_id: str = ""
    mail_key: str = ""       # AnyMail 子 key 明文(仅 emails:read、锁定本邮箱);降级为空
    mail_base_url: str = ""  # 子 key 对应的 AnyMail 服务地址,分享账号时一并给出
    saved_at: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = {
            "email": self.email,
            "password": self.password,
            "sessionKey": self.sessionKey,
            "proxy": self.proxy or "",
            "display_name": self.display_name or "",
            "mailbox_id": self.mailbox_id or "",
            "mail_key": self.mail_key or "",
            "mail_base_url": self.mail_base_url or "",
            "saved_at": self.saved_at or "",
        }
        if self.extra:
            data["extra"] = self.extra
        return data

    def text_export(self) -> str:
        """带标签的多行导出块,固定五行,空值留空保持结构稳定。"""
        return "\n".join(
            [
                f"email：{self.email or ''}",
                f"sessionkey：{self.sessionKey or ''}",
                f"proxy：{self.proxy or ''}",
                f"mailUrl：{self.mail_base_url or ''}",
                f"mailKey：{self.mail_key or ''}",
            ]
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def save_account_record(
    record: AccountRecord,
    *,
    output_dir: Path | None = None,
    accounts_jsonl: Path | None = None,
) -> dict[str, Path]:
    """写入单次 account.json / 行文本，并追加到 accounts.jsonl。

    返回写过的路径字典。
    """
    if not record.saved_at:
        record.saved_at = _now_iso()
    payload = record.to_dict()
    written: dict[str, Path] = {}

    jsonl = Path(accounts_jsonl) if accounts_jsonl else DEFAULT_ACCOUNTS_JSONL
    jsonl.parent.mkdir(parents=True, exist_ok=True)
    with jsonl.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    written["jsonl"] = jsonl

    # 同步一份易复制的文本块，块之间空行分隔
    txtl = jsonl.with_suffix(".txt")
    with txtl.open("a", encoding="utf-8") as fh:
        fh.write(record.text_export() + "\n\n")
    written["txt"] = txtl

    if output_dir is not None:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        account_json = out / "account.json"
        account_json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        written["account_json"] = account_json
        account_line = out / "account.txt"
        account_line.write_text(record.text_export() + "\n", encoding="utf-8")
        written["account_txt"] = account_line

    sk = record.sessionKey or ""
    sk_preview = f"{sk[:12]}…({len(sk)} chars)" if len(sk) > 12 else (sk or "(空)")
    log(
        f"已保存账号：email={record.email} sessionKey={sk_preview} "
        f"proxy={'有' if record.proxy else '无'} → {jsonl}"
    )
    return written
