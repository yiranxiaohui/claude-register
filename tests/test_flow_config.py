import inspect

from claude_register import browser, flow, mailbox
from claude_register.anymail import AnyMailClient


def test_flow_run_accepts_config():
    sig = inspect.signature(flow.run)
    assert "config" in sig.parameters


def test_prepare_mailbox_accepts_expires_hours():
    sig = inspect.signature(mailbox.prepare_mailbox)
    assert "expires_hours" in sig.parameters


def test_anymail_client_accepts_code_regex():
    sig = inspect.signature(AnyMailClient.__init__)
    assert "code_regex" in sig.parameters


def test_run_browser_does_not_block_on_stdin(monkeypatch):
    """跑完不再等回车。

    这行提示只打到起服务的终端、不进 sink，网页端看不见；更要命的是 input()
    在 Runner 的后台线程里会一直挂住，run 收不了尾、_active_id 不释放，
    后续任务全被 409 挡掉。
    """
    def boom(*a, **kw):
        raise AssertionError("跑完不应该再等 stdin")

    monkeypatch.setattr("builtins.input", boom)
    assert not hasattr(browser, "pause_for_user"), "pause_for_user 应已删除"
    assert "pause_for_user" not in inspect.getsource(flow)
