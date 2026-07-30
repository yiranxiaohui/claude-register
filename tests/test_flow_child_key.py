"""flow 子 key 委派接线：派生成功走子 key、失败降级、注册失败回收。"""

from __future__ import annotations

import inspect

import pytest

from claude_register import anymail, flow
from server.config_store import Config


def _cfg() -> Config:
    return Config(
        anymail_api_key="ak_parent",
        anymail_base_url="https://mail.test",
        anymail_domain="mail.test",
    )


def _mailbox() -> anymail.Mailbox:
    return anymail.Mailbox(
        id="m1", email="x@mail.test", expires_at="2026-07-31T00:00:00Z"
    )


def test_run_browser_accepts_poll_client_and_mail_key():
    sig = inspect.signature(flow.run_browser)
    assert "poll_client" in sig.parameters
    assert "mail_key" in sig.parameters
    assert sig.parameters["poll_client"].default is None
    assert sig.parameters["mail_key"].default == ""


@pytest.fixture
def wired(monkeypatch):
    """桩掉外部 IO：建邮箱、浏览器、代理校验；记录派生/回收/轮询走向。"""
    seen: dict = {"deleted": []}

    monkeypatch.setattr(flow, "validate_proxy", lambda proxy: None)
    monkeypatch.setattr(
        flow, "prepare_mailbox",
        lambda client, **kw: (_mailbox(), "2026-07-30T00:00:00Z"),
    )

    def fake_run_browser(client, mailbox, since, *, poll_client=None,
                         mail_key="", **kw):
        seen["poll_key"] = (poll_client or client).api_key
        seen["mail_key"] = mail_key
        return seen.get("browser_result")

    monkeypatch.setattr(flow, "run_browser", fake_run_browser)
    monkeypatch.setattr(
        anymail.AnyMailClient, "delete_key",
        lambda self, key_id: seen["deleted"].append(key_id),
    )
    return seen


def _mint_ok(monkeypatch):
    monkeypatch.setattr(
        anymail.AnyMailClient, "create_child_key",
        lambda self, *, email, expires_at, **kw: anymail.ChildKey(
            id="kid-1", plaintext="ak_child"
        ),
    )


def _mint_fail(monkeypatch):
    monkeypatch.setattr(
        anymail.AnyMailClient, "create_child_key",
        lambda self, *, email, expires_at, **kw: None,
    )


def test_run_polls_with_child_key_and_keeps_it_on_success(wired, monkeypatch):
    _mint_ok(monkeypatch)
    wired["browser_result"] = {"sessionKey": "sk-1"}
    flow.run(config=_cfg())
    assert wired["poll_key"] == "ak_child"
    assert wired["mail_key"] == "ak_child"
    assert wired["deleted"] == []  # 成功：子 key 随账号交付，不回收


def test_run_degrades_to_parent_key_without_export(wired, monkeypatch):
    _mint_fail(monkeypatch)
    wired["browser_result"] = {"sessionKey": "sk-1"}
    flow.run(config=_cfg())
    assert wired["poll_key"] == "ak_parent"
    assert wired["mail_key"] == ""  # 父 key 绝不进导出
    assert wired["deleted"] == []


def test_run_revokes_child_when_registration_fails(wired, monkeypatch):
    _mint_ok(monkeypatch)
    wired["browser_result"] = None  # 没拿到 sessionKey
    flow.run(config=_cfg())
    assert wired["deleted"] == ["kid-1"]


def test_run_revokes_child_when_browser_raises(wired, monkeypatch):
    _mint_ok(monkeypatch)

    def boom(*a, **kw):
        raise RuntimeError("browser exploded")

    monkeypatch.setattr(flow, "run_browser", boom)
    with pytest.raises(RuntimeError):
        flow.run(config=_cfg())
    assert wired["deleted"] == ["kid-1"]
