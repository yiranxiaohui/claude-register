"""接管窗口自适应：纯规划逻辑 + 真实 Xvfb 集成（缺 Xvfb 时跳过）。"""
from __future__ import annotations

import os
import shutil
import subprocess
import time

import pytest

from server.window_fit import (
    MAX_FIT,
    XVFB_MAX,
    TopLevel,
    WindowFitter,
    fit_size,
    plan_fit,
)


def _win(id=1, instance="Navigator", viewable=True, override=False, geo=(0, 0, 1280, 900)):
    return TopLevel(id, instance, viewable, override, *geo)


def test_fit_size_matches_client_desktop():
    assert fit_size((1672, 855)) == (1672, 855)


def test_fit_size_skips_until_xpra_client_resizes_desktop():
    # 客户端连上前根窗口还是 Xvfb 上限，铺满只会开出巨型窗口
    assert fit_size(XVFB_MAX) is None
    assert fit_size((0, 0)) is None


def test_fit_size_capped():
    assert fit_size((5000, 3000)) == MAX_FIT


def test_plan_resizes_browser_main_window():
    # 截图里的场景：桌面 1672x855，浏览器窗口还是启动时的 1280x900
    assert plan_fit((1672, 855), [_win(id=7)]) == [(7, (1672, 855))]


def test_plan_moves_window_back_to_origin():
    assert plan_fit((1672, 855), [_win(geo=(10, 20, 1672, 855))]) == [(1, (1672, 855))]


def test_plan_skips_already_fitted_window():
    assert plan_fit((1672, 855), [_win(geo=(0, 0, 1672, 855))]) == []


def test_plan_ignores_menus_hidden_and_non_browser_windows():
    windows = [
        _win(id=2, override=True),            # 下拉框/右键菜单：浏览器自己定位
        _win(id=3, viewable=False),           # 未映射
        _win(id=4, instance="Camoufox"),      # Camoufox 的 288x56 辅助窗口
        _win(id=5),                           # 真正的浏览器主窗口
    ]
    assert plan_fit((1920, 1040), windows) == [(5, (1920, 1040))]


def test_plan_noop_before_client_connects():
    assert plan_fit(XVFB_MAX, [_win()]) == []


def test_fitter_stop_is_safe_without_start():
    WindowFitter(":999").stop()


# ---- 真实 X server 集成 ----


@pytest.fixture
def xvfb():
    if not shutil.which("Xvfb"):
        pytest.skip("需要 Xvfb")
    pytest.importorskip("Xlib")
    disp = ":%d" % (90 + os.getpid() % 800)
    proc = subprocess.Popen(
        ["Xvfb", disp, "-screen", "0", "1672x855x24", "-nolisten", "tcp", "-ac"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    sock = f"/tmp/.X11-unix/X{disp[1:]}"
    deadline = time.time() + 10
    while not os.path.exists(sock) and time.time() < deadline:
        time.sleep(0.05)
    try:
        yield disp
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def _wait(fn, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if fn():
            return True
        time.sleep(0.05)
    return False


def test_fitter_resizes_real_browser_window_but_not_menus(xvfb):
    from Xlib import X, display

    d = display.Display(xvfb)
    root = d.screen().root

    def make(instance, w, h, override=False):
        win = root.create_window(
            0, 0, w, h, 0, d.screen().root_depth, X.InputOutput, X.CopyFromParent,
            override_redirect=override,
        )
        win.set_wm_class(instance, "camoufox")
        win.map()
        return win

    browser = make("Navigator", 1280, 900)
    menu = make("Navigator", 200, 300, override=True)
    d.sync()

    fitter = WindowFitter(xvfb, interval=0.05).start()
    try:
        def geo(w):
            g = w.get_geometry()
            return (g.x, g.y, g.width, g.height)

        assert _wait(lambda: geo(browser) == (0, 0, 1672, 855)), geo(browser)
        assert geo(menu) == (0, 0, 200, 300)  # override-redirect 菜单不被拉伸

        # 新开的浏览器窗口（如弹窗）同样会被铺满
        second = make("Navigator", 640, 480)
        d.sync()
        assert _wait(lambda: geo(second) == (0, 0, 1672, 855)), geo(second)
    finally:
        fitter.stop()
        d.close()
