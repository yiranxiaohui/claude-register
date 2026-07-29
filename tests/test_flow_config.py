import inspect

from claude_register import flow, mailbox
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
