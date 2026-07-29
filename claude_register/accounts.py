"""注册成功后的账号落盘：email / password / sessionKey / proxy。"""

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
            "saved_at": self.saved_at or "",
        }
        if self.extra:
            data["extra"] = self.extra
        return data

    def line_export(self) -> str:
        """常见账号导出格式：email----password----sessionKey----proxy"""
        return "----".join(
            [
                self.email or "",
                self.password or "",
                self.sessionKey or "",
                self.proxy or "",
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

    # 同步一份易复制的文本行
    txtl = jsonl.with_suffix(".txt")
    with txtl.open("a", encoding="utf-8") as fh:
        fh.write(record.line_export() + "\n")
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
        account_line.write_text(record.line_export() + "\n", encoding="utf-8")
        written["account_txt"] = account_line

    sk = record.sessionKey or ""
    sk_preview = f"{sk[:12]}…({len(sk)} chars)" if len(sk) > 12 else (sk or "(空)")
    log(
        f"已保存账号：email={record.email} sessionKey={sk_preview} "
        f"proxy={'有' if record.proxy else '无'} → {jsonl}"
    )
    return written
