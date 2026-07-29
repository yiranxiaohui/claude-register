"""账号落盘与 sessionKey 提取。"""

from __future__ import annotations

import json

from claude_register.accounts import AccountRecord, save_account_record
from claude_register import browser


class _Ctx:
    def __init__(self, cookies):
        self._cookies = cookies

    def cookies(self):
        return self._cookies


class _Page:
    def __init__(self, cookies):
        self.context = _Ctx(cookies)
        self.waited = []

    def wait_for_timeout(self, ms):
        self.waited.append(ms)


def test_extract_session_key_prefers_exact_name():
    page = _Page(
        [
            {"name": "session_key", "value": "other"},
            {"name": "sessionKey", "value": "sk-ant-sid01-abc"},
        ]
    )
    assert browser.extract_session_key(page) == "sk-ant-sid01-abc"


def test_extract_session_key_missing():
    assert browser.extract_session_key(_Page([{"name": "other", "value": "x"}])) is None


def test_wait_for_session_key_polls(monkeypatch):
    calls = {"n": 0}

    def fake_extract(page):
        calls["n"] += 1
        if calls["n"] < 3:
            return None
        return "sk-ant-ok"

    monkeypatch.setattr(browser, "extract_session_key", fake_extract)
    page = _Page([])
    assert browser.wait_for_session_key(page, timeout_ms=5_000) == "sk-ant-ok"
    assert calls["n"] >= 3


def test_save_account_record_writes_json_and_jsonl(tmp_path):
    rec = AccountRecord(
        email="a@x.com",
        password="",
        sessionKey="sk-ant-test",
        proxy="socks5://u:p@host:1",
        display_name="Alex",
        mailbox_id="m1",
    )
    jsonl = tmp_path / "accounts.jsonl"
    out = tmp_path / "run1"
    paths = save_account_record(rec, output_dir=out, accounts_jsonl=jsonl)
    assert paths["jsonl"] == jsonl
    row = json.loads(jsonl.read_text(encoding="utf-8").strip())
    assert row["email"] == "a@x.com"
    assert row["sessionKey"] == "sk-ant-test"
    assert row["proxy"].startswith("socks5://")
    assert row["password"] == ""
    data = json.loads((out / "account.json").read_text(encoding="utf-8"))
    assert data["sessionKey"] == "sk-ant-test"
    line = (out / "account.txt").read_text(encoding="utf-8").strip()
    # email----password----sessionKey----proxy；password 为空时是连续分隔符
    assert line == "a@x.com--------sk-ant-test----socks5://u:p@host:1"
    assert rec.line_export() == line


def test_account_record_line_export_with_password():
    rec = AccountRecord(
        email="u@d.com",
        password="secret",
        sessionKey="sk",
        proxy="http://p",
    )
    assert rec.line_export() == "u@d.com----secret----sk----http://p"
