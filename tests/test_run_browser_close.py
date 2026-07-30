"""run_browser 结尾 context.close() 崩溃不能把已保存的账号变成异常吞掉。

复现路径：登录/建号收尾后 _capture() 已经成功保存账号（account 非 None），
但 `with browser_session(...)` 退出前的 `context.close()` 本身抛异常
（比如目标进程已经先挂了）。旧代码没有包 try/except，这个异常会直接冲出
run_browser，被 flow.run() 的 `except BaseException` 捕获，进而误判成
「注册中断」，把刚刚导出的子 key 撤销掉——即便账号其实已经落盘成功。
"""

from __future__ import annotations

from contextlib import contextmanager

from claude_register import flow
from claude_register.anymail import AnyMailClient, Mailbox


def _client() -> AnyMailClient:
    return AnyMailClient(
        base_url="https://mail.test", api_key="ak_parent", domain="mail.test"
    )


def _mailbox() -> Mailbox:
    return Mailbox(id="m1", email="x@mail.test", expires_at="2026-07-31T00:00:00Z")


class _FakeContext:
    def __init__(self, *, raise_on_close: bool):
        self.raise_on_close = raise_on_close
        self.closed = False

    def close(self):
        self.closed = True
        if self.raise_on_close:
            raise RuntimeError("context.close 崩了")


class _FakePage:
    pass


def _wire_common(monkeypatch, *, ctx):
    monkeypatch.setattr(flow, "open_login", lambda page: None)
    monkeypatch.setattr(flow, "wait_login_form", lambda page: None)
    monkeypatch.setattr(flow, "fill_email", lambda page, email: None)
    monkeypatch.setattr(flow, "wait_code_screen", lambda page: True)
    monkeypatch.setattr(flow, "open_magic_link", lambda page, link: True)
    monkeypatch.setattr(flow, "finish_after_auth", lambda page, display_name=None: True)
    monkeypatch.setattr(flow, "wait_for_session_key", lambda page, timeout_ms=30_000: "sk-ant-ok")

    def fake_save(record, *, output_dir=None):
        return {}

    monkeypatch.setattr(flow, "save_account_record", fake_save)

    @contextmanager
    def fake_browser_session(proxy=None):
        yield "BROWSER"

    monkeypatch.setattr(flow, "browser_session", fake_browser_session)
    monkeypatch.setattr(flow, "new_page", lambda browser: (ctx, _FakePage()))


class _Poll:
    """open_magic_link/finish_after_auth/wait_for_session_key 全部打桩成功，
    link 内容本身不再被解析，随便给一个非空字符串即可触发 _capture 分支。"""

    def poll_magic_link(self, *, to, since, timeout):
        return "http://claude.ai/magic-link#stub"

    def poll_code(self, *, to, since, timeout):
        return None


def test_context_close_raising_does_not_escape_run_browser(monkeypatch):
    ctx = _FakeContext(raise_on_close=True)
    _wire_common(monkeypatch, ctx=ctx)

    account = flow.run_browser(
        _client(),
        _mailbox(),
        "2026-07-30T00:00:00Z",
        auto_login=True,
        code_timeout=5.0,
        poll_client=_Poll(),
    )

    assert ctx.closed is True
    assert account is not None
    assert account["sessionKey"] == "sk-ant-ok"


def test_context_close_not_raising_still_works(monkeypatch):
    ctx = _FakeContext(raise_on_close=False)
    _wire_common(monkeypatch, ctx=ctx)

    account = flow.run_browser(
        _client(),
        _mailbox(),
        "2026-07-30T00:00:00Z",
        auto_login=True,
        code_timeout=5.0,
        poll_client=_Poll(),
    )

    assert ctx.closed is True
    assert account is not None
