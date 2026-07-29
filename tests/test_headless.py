"""headless 档位的平台自适应。

Camoufox 的 headless="virtual" 就是 Xvfb（X11 虚拟帧缓冲）：它 Popen 一个
Xvfb 进程再把 DISPLAY 塞进环境变量。camoufox 自己在 virtdisplay.py 里
assert_linux() 直接拦掉非 Linux 平台：

    VirtualDisplayNotSupported: Virtual display is only supported on Linux.

Windows 上的 camoufox.exe 是原生 Win32 构建，根本不走 X11，DISPLAY 对它没有
任何意义——所以 "virtual" 不是「还没适配」，是概念上不存在。但它要解决的问题
（无显示器的机器上不想用真 headless，指纹太弱）在有桌面的平台上本来就不存在，
直接 headless=False 即可，效果比 Xvfb 还好。
"""

from __future__ import annotations

import pytest

from claude_register import browser


@pytest.fixture
def fake_camoufox(monkeypatch):
    """记录传给 Camoufox 的 kwargs。"""
    seen: dict = {}

    class FakeCamoufox:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def __enter__(self):
            return "BROWSER"

        def __exit__(self, *exc):
            return None

    monkeypatch.setattr(browser, "Camoufox", FakeCamoufox)
    return seen


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
    """Windows/macOS 桌面本来就是真显示器，直接 headless=False——
    这正是 virtual 想模拟的东西，而且是真的。"""
    monkeypatch.setattr(browser.sys, "platform", platform)
    assert browser.pick_headless() is False


def test_windows_never_picks_virtual_even_if_xvfb_on_path(monkeypatch):
    """光看 which("Xvfb") 不够：camoufox 拦的是 OS_NAME != 'lin'，
    不是「找不到 Xvfb」。装了 WSL/Cygwin 的 Windows 上 which 可能真的命中，
    那时选 virtual 依然会崩。"""
    monkeypatch.setattr(browser.sys, "platform", "win32")
    monkeypatch.setattr(browser.shutil, "which", lambda name: r"C:\tools\Xvfb.exe")
    assert browser.pick_headless() is False


def test_browser_session_uses_picked_headless_not_hardcoded(monkeypatch, fake_camoufox):
    """browser_session 必须用探测结果，而不是写死的 "virtual"。"""
    monkeypatch.setattr(browser, "pick_headless", lambda: False)

    with browser.browser_session():
        pass

    assert fake_camoufox["headless"] is False


def test_startup_log_reports_actual_mode(monkeypatch, fake_camoufox):
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

    assert not any("headless=virtual" in line for line in captured), (
        f"没跑 virtual 却说自己在跑 virtual：{captured}"
    )


def test_launch_failure_hint_omits_xvfb_on_windows(monkeypatch):
    """Windows 上启动失败时提示「确认已安装 Xvfb」是指错方向——
    那平台上装了也没用。"""
    monkeypatch.setattr(browser, "pick_headless", lambda: False)

    def _boom(**kwargs):
        class Boom:
            def __enter__(self):
                raise OSError("启动失败")

            def __exit__(self, *exc):
                return None

        return Boom()

    monkeypatch.setattr(browser, "Camoufox", _boom)

    with pytest.raises(RuntimeError) as exc:
        with browser.browser_session():
            pass

    assert "Xvfb" not in str(exc.value), f"Windows 路径不该提 Xvfb：{exc.value}"
    assert "camoufox fetch" in str(exc.value), "该提的二进制下载还是要提"


def test_launch_failure_hint_keeps_xvfb_when_virtual(monkeypatch):
    """反过来，真的在跑 virtual 时那句提示是对的，别误删。"""
    monkeypatch.setattr(browser, "pick_headless", lambda: "virtual")

    def _boom(**kwargs):
        class Boom:
            def __enter__(self):
                raise OSError("启动失败")

            def __exit__(self, *exc):
                return None

        return Boom()

    monkeypatch.setattr(browser, "Camoufox", _boom)

    with pytest.raises(RuntimeError) as exc:
        with browser.browser_session():
            pass

    assert "Xvfb" in str(exc.value)
