"""headless 档位的平台自适应，以及 browser_session 如何据此启动 Chromium。

"virtual" = 自管一个 Xvfb（X11 虚拟帧缓冲），Chromium 以有头模式挂上去；它只在
Linux 上有意义。有桌面的平台直接 headless=False 用真显示器，效果比 Xvfb 还好。
"""

from __future__ import annotations

import pytest

from claude_register import browser


def test_linux_with_xvfb_uses_virtual(monkeypatch):
    """容器里的既有路径不能被改坏：Linux + 装了 Xvfb 就该继续用 virtual。"""
    monkeypatch.setattr(browser.sys, "platform", "linux")
    monkeypatch.setattr(browser.shutil, "which", lambda name: "/usr/bin/Xvfb")
    assert browser.pick_headless() == "virtual"


def test_linux_without_xvfb_falls_back_to_real_headless(monkeypatch):
    """没装 Xvfb 的 Linux（无桌面）只剩真 headless 这一条路。
    指纹弱一档，但总好过启动直接崩。"""
    monkeypatch.setattr(browser.sys, "platform", "linux")
    monkeypatch.setattr(browser.shutil, "which", lambda name: None)
    assert browser.pick_headless() is True


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_desktop_platforms_use_windowed_mode(monkeypatch, platform):
    """Windows/macOS 桌面本来就是真显示器，直接 headless=False。"""
    monkeypatch.setattr(browser.sys, "platform", platform)
    assert browser.pick_headless() is False


def test_windows_never_picks_virtual_even_if_xvfb_on_path(monkeypatch):
    """光看 which("Xvfb") 不够：装了 WSL/Cygwin 的 Windows 上 which 可能真的命中，
    但原生 Windows 浏览器不走 X11。"""
    monkeypatch.setattr(browser.sys, "platform", "win32")
    monkeypatch.setattr(browser.shutil, "which", lambda name: r"C:\tools\Xvfb.exe")
    assert browser.pick_headless() is False


def test_virtual_mode_starts_xvfb_and_runs_headed(monkeypatch, fake_chromium):
    """virtual 档：先起自管 Xvfb，再以有头模式挂到那个显示上；退出时两者都收掉。"""
    monkeypatch.setattr(browser, "pick_headless", lambda: "virtual")

    with browser.browser_session() as b:
        assert b == "BROWSER"

    assert fake_chromium["headless"] is False
    assert fake_chromium["display"] == ":150"
    assert len(fake_chromium["displays"]) == 1
    assert fake_chromium["displays"][0].stopped
    assert fake_chromium["session"].closed


def test_windowed_mode_uses_no_virtual_display(monkeypatch, fake_chromium):
    monkeypatch.setattr(browser, "pick_headless", lambda: False)

    with browser.browser_session():
        pass

    assert fake_chromium["headless"] is False
    assert fake_chromium["display"] is None
    assert fake_chromium["displays"] == []


def test_real_headless_fallback(monkeypatch, fake_chromium):
    monkeypatch.setattr(browser, "pick_headless", lambda: True)

    with browser.browser_session():
        pass

    assert fake_chromium["headless"] is True
    assert fake_chromium["display"] is None


def test_startup_log_reports_actual_mode(monkeypatch, fake_chromium):
    """启动日志不能写死「headless=virtual」——Windows 上那是句假话，
    排查问题时会把人带偏。"""
    from claude_register import console

    monkeypatch.setattr(browser, "pick_headless", lambda: False)
    captured: list[str] = []
    token = console.set_sink(captured.append)
    try:
        with browser.browser_session():
            pass
    finally:
        console.reset_sink(token)

    assert any("Chromium" in line and "headless=False" in line for line in captured), captured


def _boom(**kwargs):
    raise OSError("启动失败")


def test_launch_failure_hint_omits_xvfb_on_windows(monkeypatch, fake_chromium):
    """Windows 上启动失败时提示「确认已安装 Xvfb」是指错方向——
    那平台上装了也没用。"""
    monkeypatch.setattr(browser, "pick_headless", lambda: False)
    monkeypatch.setattr(browser, "launch_chromium", _boom)

    with pytest.raises(RuntimeError) as exc:
        with browser.browser_session():
            pass

    assert "Xvfb" not in str(exc.value), f"Windows 路径不该提 Xvfb：{exc.value}"
    assert "playwright install chromium" in str(exc.value), "该提的浏览器下载还是要提"


def test_launch_failure_hint_keeps_xvfb_when_virtual(monkeypatch, fake_chromium):
    """反过来，真的在跑 virtual 时那句提示是对的，别误删；虚拟显示也要收掉。"""
    monkeypatch.setattr(browser, "pick_headless", lambda: "virtual")
    monkeypatch.setattr(browser, "launch_chromium", _boom)

    with pytest.raises(RuntimeError) as exc:
        with browser.browser_session():
            pass

    assert "Xvfb" in str(exc.value)
    assert fake_chromium["displays"][0].stopped, "启动失败也不能泄漏 Xvfb 进程"


def test_chromium_args_hide_automation_and_protect_proxy():
    args = browser.chromium_args(display=":150", proxied=True)
    assert "--disable-blink-features=AutomationControlled" in args
    assert "--ozone-platform=x11" in args, "挂 Xvfb 时必须强制 X11，不能跑到宿主 Wayland 桌面"
    assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in args, (
        "走代理时 WebRTC UDP 会泄漏服务器真实 IP"
    )
    assert "--enable-automation" in browser.CHROMIUM_IGNORE_DEFAULT_ARGS, (
        "--enable-automation 会让 navigator.webdriver=true"
    )


def test_chromium_args_without_display_or_proxy():
    args = browser.chromium_args(display=None, proxied=False)
    assert "--ozone-platform=x11" not in args
    assert not any(a.startswith("--force-webrtc-ip-handling-policy") for a in args)


def test_launch_chromium_passes_env_and_proxy(monkeypatch):
    """DISPLAY/TZ 走进程环境；Wayland 变量要去掉，否则开发机上会开到真实桌面。"""
    seen = {}

    class FakeChromium:
        def launch(self, **kwargs):
            seen.update(kwargs)
            return "BROWSER"

    class FakePlaywright:
        chromium = FakeChromium()

        def stop(self):
            seen["stopped"] = True

    class FakeManager:
        def start(self):
            return FakePlaywright()

    monkeypatch.setattr(browser, "sync_playwright", lambda: FakeManager())
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")

    session = browser.launch_chromium(
        headless=False, proxy={"server": "socks5://127.0.0.1:1"},
        display=":150", timezone="Asia/Tokyo",
    )

    assert session.browser == "BROWSER"
    assert seen["channel"] == "chromium"
    assert seen["proxy"] == {"server": "socks5://127.0.0.1:1"}
    assert seen["env"]["DISPLAY"] == ":150"
    assert seen["env"]["TZ"] == "Asia/Tokyo"
    assert seen["env"]["LANG"] == "en_US.UTF-8"
    assert "WAYLAND_DISPLAY" not in seen["env"]


def test_launch_chromium_stops_playwright_on_failure(monkeypatch):
    stopped = []

    class FakeChromium:
        def launch(self, **kwargs):
            raise OSError("no browser")

    class FakePlaywright:
        chromium = FakeChromium()

        def stop(self):
            stopped.append(True)

    class FakeManager:
        def start(self):
            return FakePlaywright()

    monkeypatch.setattr(browser, "sync_playwright", lambda: FakeManager())
    with pytest.raises(OSError):
        browser.launch_chromium(headless=True)
    assert stopped == [True], "启动失败也要停掉 Playwright driver 进程"


def test_browser_proxy_pool_limits_stay_below_relay_slots():
    """浏览器自己的代理连接池要比中继槽位小，留出余量；Chromium 下限为 6。"""
    assert browser.browser_proxy_pool_limits(16) == (12, 6)
    assert browser.browser_proxy_pool_limits(32) == (24, 8)
    assert browser.browser_proxy_pool_limits(4) == (6, 6)


def test_launch_chromium_with_pool_limits_uses_temp_profile(monkeypatch):
    """连接池上限只能写进 profile 的 Local State，所以改用临时 profile 的持久化上下文。"""
    import json
    import os

    seen = {}

    class FakeContext:
        browser = "BROWSER"

        def close(self):
            seen["context_closed"] = True

    class FakeChromium:
        def launch(self, **kwargs):
            raise AssertionError("设了连接池上限就不能走普通 launch")

        def launch_persistent_context(self, user_data_dir, **kwargs):
            seen["profile"] = user_data_dir
            seen["kwargs"] = kwargs
            with open(os.path.join(user_data_dir, "Local State"), encoding="utf-8") as f:
                seen["state"] = json.load(f)
            return FakeContext()

    class FakePlaywright:
        chromium = FakeChromium()

        def stop(self):
            seen["stopped"] = True

    class FakeManager:
        def start(self):
            return FakePlaywright()

    monkeypatch.setattr(browser, "sync_playwright", lambda: FakeManager())

    session = browser.launch_chromium(
        headless=False, proxy={"server": "socks5://127.0.0.1:1"}, proxy_pool_limits=(12, 6),
    )

    assert seen["state"] == {"net": {
        "max_connections_per_proxy": 12,
        "max_connections_per_proxy_for_websocket": 6,
        "allow_socket_pool_size_randomization_for_proxies": False,
    }}
    assert seen["kwargs"]["no_viewport"] is True
    assert seen["kwargs"]["proxy"] == {"server": "socks5://127.0.0.1:1"}
    assert seen["kwargs"]["channel"] == "chromium"
    assert session.browser == "BROWSER"
    assert isinstance(session.context, FakeContext)
    assert os.path.isdir(seen["profile"])

    session.close()
    assert seen["context_closed"] and seen["stopped"]
    assert not os.path.exists(seen["profile"]), "关闭后要删除临时 profile"


def test_launch_chromium_with_pool_limits_cleans_profile_on_failure(monkeypatch):
    import os

    seen = {}

    class FakeChromium:
        def launch_persistent_context(self, user_data_dir, **kwargs):
            seen["profile"] = user_data_dir
            raise OSError("no browser")

    class FakePlaywright:
        chromium = FakeChromium()

        def stop(self):
            seen["stopped"] = True

    class FakeManager:
        def start(self):
            return FakePlaywright()

    monkeypatch.setattr(browser, "sync_playwright", lambda: FakeManager())
    with pytest.raises(OSError):
        browser.launch_chromium(headless=True, proxy_pool_limits=(6, 6))
    assert seen["stopped"]
    assert not os.path.exists(seen["profile"])
