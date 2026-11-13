"""网页导航失败不能销毁可供手动刷新、继续操作的接管浏览器。"""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from server import takeover_browser
from server.takeover import TakeoverError, TakeoverManager


@pytest.fixture
def takeover(monkeypatch):
    session = Mock()
    context = session.context
    context.pages = []
    page = context.new_page.return_value
    page.context = context
    context.browser = session.browser
    page.is_closed.return_value = False
    session.browser.is_connected.return_value = True
    relay = Mock()
    monkeypatch.setattr(
        takeover_browser, "build_proxy_options",
        lambda *args, **kwargs: ({"server": "socks5://127.0.0.1:1"}, relay, None),
    )
    monkeypatch.setattr(takeover_browser, "launch_chromium", lambda **kwargs: session)
    prefill = Mock()
    monkeypatch.setattr(takeover_browser, "_prefill_login_email", prefill)
    log = Mock()
    monkeypatch.setattr(takeover_browser, "log", log)
    launcher = Mock()
    manager = TakeoverManager(
        now_fn=lambda: "2026-11-07T00:00:00Z",
        launcher=launcher,
        wait_display_fn=lambda display: None,
        wait_web_fn=lambda host, port: None,
        window_fitter_fn=lambda display: Mock(),
    )
    yield SimpleNamespace(
        session=session, context=context, page=page, relay=relay,
        prefill=prefill, log=log, launcher=launcher, manager=manager,
    )
    manager.stop()


@pytest.mark.parametrize("mode", ["account", "manual"])
@pytest.mark.parametrize("error_type,message", [
    (PlaywrightTimeoutError, "Page.goto: Timeout 45000ms exceeded."),
    (PlaywrightError, "Page.goto: net::ERR_CONNECTION_CLOSED at https://claude.ai/"),
    (PlaywrightError, "Page.goto: net::ERR_PROXY_CONNECTION_FAILED"),
])
def test_navigation_failure_keeps_takeover_running(takeover, mode, error_type, message):
    takeover.page.goto.side_effect = error_type(message)
    takeover.manager.start(
        email="a@example.com", session_key="sk-test" if mode == "account" else "",
        mode=mode, login_email="a@example.com",
    )

    assert takeover.manager.status()["running"] is True
    takeover.session.close.assert_not_called()
    takeover.relay.stop.assert_not_called()
    takeover.launcher.spawn.return_value.terminate.assert_not_called()
    takeover.prefill.assert_not_called()
    assert any("可在接管画面中手动刷新" in call.args[0] for call in takeover.log.call_args_list)
    if mode == "account":
        assert takeover.context.add_cookies.call_args.args[0][0]["value"] == "sk-test"
    else:
        takeover.context.add_cookies.assert_not_called()

    takeover.manager.stop()
    takeover.session.close.assert_called_once()
    takeover.relay.stop.assert_called_once()
    takeover.launcher.spawn.return_value.terminate.assert_called_once()


@pytest.mark.parametrize("failure", ["cookies", "page_closed", "browser_disconnected"])
def test_unusable_browser_still_fails_and_cleans_up(takeover, failure):
    error = PlaywrightError("browser setup failed")
    if failure == "cookies":
        takeover.context.add_cookies.side_effect = error
    else:
        takeover.page.goto.side_effect = error
        if failure == "page_closed":
            takeover.page.is_closed.return_value = True
        else:
            takeover.session.browser.is_connected.return_value = False

    with pytest.raises(TakeoverError, match="browser setup failed"):
        takeover.manager.start(email="a@example.com", session_key="sk-test")

    assert takeover.manager.status()["running"] is False
    takeover.session.close.assert_called_once()
    takeover.relay.stop.assert_called_once()
    takeover.launcher.spawn.return_value.terminate.assert_called_once()


def test_manual_login_prefills_email_after_successful_navigation(takeover):
    takeover.manager.start(
        email="", session_key="", mode="manual", login_email="a@example.com",
    )
    assert takeover.manager.status()["running"] is True
    takeover.prefill.assert_called_once_with(takeover.page, "a@example.com")
