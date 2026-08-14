import pytest

from server import takeover_browser


class _Context:
    def __init__(self):
        self.cleared = []

    def clear_cookies(self, **kwargs):
        self.cleared.append(kwargs)


class _Page:
    def __init__(self):
        self.context = _Context()
        self.waits = []

    def wait_for_timeout(self, timeout):
        self.waits.append(timeout)


class _MailClient:
    def __init__(self, *, base_url, api_key):
        self.base_url = base_url
        self.api_key = api_key
        self.magic_calls = []

    def poll_magic_link(self, **kwargs):
        self.magic_calls.append(kwargs)
        return "https://claude.ai/magic-link#token"

    def poll_code(self, **kwargs):
        raise AssertionError("有魔术链接时不应轮询验证码")


def test_browser_handle_relogin_clears_old_cookie_and_returns_new_key(monkeypatch):
    page = _Page()
    client_holder = {}

    def client_factory(**kwargs):
        client = _MailClient(**kwargs)
        client_holder["client"] = client
        return client

    calls = []
    monkeypatch.setattr(takeover_browser, "AnyMailClient", client_factory)
    monkeypatch.setattr(takeover_browser, "utc_now_iso", lambda: "2026-08-14T00:00:00Z")
    monkeypatch.setattr(takeover_browser, "open_login", lambda p: calls.append("open_login"))
    monkeypatch.setattr(takeover_browser, "wait_login_form", lambda p: calls.append("form"))
    monkeypatch.setattr(
        takeover_browser,
        "fill_email",
        lambda p, email: calls.append(("email", email)),
    )
    monkeypatch.setattr(takeover_browser, "wait_code_screen", lambda p: True)
    monkeypatch.setattr(takeover_browser, "open_magic_link", lambda p, link: True)
    monkeypatch.setattr(takeover_browser, "wait_for_session_key", lambda *a, **k: "sk-new")

    handle = takeover_browser._BrowserHandle(None, None, page)
    session_key = handle.relogin(
        email="a@x.com",
        mail_base_url="https://mail.test",
        mail_api_key="ak_child",
        login_timeout=120,
    )

    assert session_key == "sk-new"
    assert page.context.cleared == [{"name": "sessionKey"}]
    assert calls == ["open_login", "form", ("email", "a@x.com")]
    client = client_holder["client"]
    assert client.base_url == "https://mail.test"
    assert client.api_key == "ak_child"
    assert client.magic_calls[0]["since"] == "2026-08-14T00:00:00Z"


def test_browser_handle_relogin_reports_missing_new_session(monkeypatch):
    page = _Page()
    monkeypatch.setattr(takeover_browser, "AnyMailClient", _MailClient)
    monkeypatch.setattr(takeover_browser, "open_login", lambda p: None)
    monkeypatch.setattr(takeover_browser, "wait_login_form", lambda p: None)
    monkeypatch.setattr(takeover_browser, "fill_email", lambda p, email: None)
    monkeypatch.setattr(takeover_browser, "wait_code_screen", lambda p: True)
    monkeypatch.setattr(takeover_browser, "open_magic_link", lambda p, link: True)
    monkeypatch.setattr(takeover_browser, "wait_for_session_key", lambda *a, **k: None)

    handle = takeover_browser._BrowserHandle(None, None, page)
    with pytest.raises(RuntimeError, match="可能已被封"):
        handle.relogin(
            email="a@x.com",
            mail_base_url="https://mail.test",
            mail_api_key="ak_child",
            login_timeout=120,
        )
