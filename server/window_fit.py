"""接管桌面没有窗口管理器：让浏览器主窗口始终铺满 Xpra 桌面。

为什么需要它：接管用 `xpra start-desktop` + `--resize-display`，Xpra 会把 Xvfb
根窗口缩放到网页客户端的画布尺寸（随浏览器窗口变化）。但桌面里没有窗口管理器，
Camoufox 按启动参数 `window=(1280, 900)` 开出窗口后就再没人去调它：画布比它宽时
右侧一片黑，比它矮时底部（输入框）被裁掉。

实测把 X 窗口调到桌面尺寸后，innerWidth/innerHeight、visualViewport 与 CSS
媒体查询都随真实尺寸变化，页面按新视口正常排版；只有 outerWidth/outerHeight
仍是 Camoufox 伪造的启动值——接管是人工操作已登录会话，可以接受。

实现取最简单可靠的轮询：每隔 POLL_INTERVAL 看一眼根窗口尺寸和顶层窗口，
不一致就 configure。新开窗口、客户端调尺寸、Firefox 自己改尺寸都能收敛，
且没有事件订阅/事件循环要维护。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

from claude_register import console

POLL_INTERVAL = 0.3
# Xvfb 启动时的 -screen 尺寸（见 takeover.py）。Xpra 客户端连上之前根窗口就是
# 这个上限值；此时铺满会开出一个 8192x4096 的巨型窗口，白吃内存，跳过等客户端。
XVFB_MAX = (8192, 4096)
# 单窗口尺寸上限：超大屏也够用，防止异常尺寸把渲染内存撑爆。
MAX_FIT = (3840, 2160)
# Firefox/Camoufox 浏览器主窗口的 WM_CLASS instance。
BROWSER_INSTANCE = "Navigator"


@dataclass(frozen=True)
class TopLevel:
    """顶层窗口快照（与 Xlib 解耦，便于纯函数测试）。"""

    id: int
    instance: str
    viewable: bool
    override_redirect: bool
    x: int
    y: int
    width: int
    height: int


def fit_size(root: tuple[int, int]) -> tuple[int, int] | None:
    """根窗口尺寸 → 浏览器窗口目标尺寸；None 表示暂不调整。"""
    width, height = root
    if (width, height) == XVFB_MAX or width <= 0 or height <= 0:
        return None
    return min(width, MAX_FIT[0]), min(height, MAX_FIT[1])


def plan_fit(root: tuple[int, int], windows) -> list[tuple[int, tuple[int, int]]]:
    """返回需要调整的 (窗口 id, (宽, 高))，目标位置固定 (0, 0)。

    只动可见的浏览器主窗口；override-redirect 窗口（下拉框、右键菜单、tooltip）
    由浏览器自己定位，绝不能碰。已经铺满的窗口不重复 configure，避免无谓重绘。
    """
    target = fit_size(root)
    if target is None:
        return []
    plan = []
    for win in windows:
        if not win.viewable or win.override_redirect or win.instance != BROWSER_INSTANCE:
            continue
        if (win.x, win.y, win.width, win.height) == (0, 0, *target):
            continue
        plan.append((win.id, target))
    return plan


class WindowFitter:
    """后台线程：持续把浏览器主窗口铺满指定 X display 的根窗口。"""

    def __init__(self, display: str, *, interval: float = POLL_INTERVAL):
        self.display = display
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> WindowFitter:
        self._thread = threading.Thread(
            target=self._run, name="takeover-window-fit", daemon=True,
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _run(self) -> None:
        try:
            from Xlib import display as xdisplay
        except Exception as exc:  # noqa: BLE001
            console.log(f"接管窗口自适应不可用（缺少 python-xlib）：{exc}")
            return
        conn = None
        warned = False
        while not self._stop.is_set():
            try:
                if conn is None:
                    conn = xdisplay.Display(self.display)
                self._tick(conn)
            except Exception as exc:  # noqa: BLE001 — 窗口瞬间消失等都在这里兜住
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:  # noqa: BLE001
                        pass
                    conn = None
                if not warned and not self._stop.is_set():
                    console.log(f"接管窗口自适应暂时失败，将自动重试：{type(exc).__name__}")
                    warned = True
            self._stop.wait(self.interval)
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _snapshot(conn) -> tuple[tuple[int, int], list[TopLevel]]:
        from Xlib import X
        from Xlib import error as xerror

        root = conn.screen().root
        geo = root.get_geometry()
        windows = []
        for child in root.query_tree().children:
            try:
                attrs = child.get_attributes()
                if attrs.map_state != X.IsViewable:
                    continue
                cls = child.get_wm_class() or ("", "")
                g = child.get_geometry()
            except (xerror.BadWindow, xerror.BadDrawable):
                continue  # 窗口在枚举与查询之间被销毁
            windows.append(TopLevel(
                id=child.id, instance=cls[0], viewable=True,
                override_redirect=bool(attrs.override_redirect),
                x=g.x, y=g.y, width=g.width, height=g.height,
            ))
        return (geo.width, geo.height), windows

    def _tick(self, conn) -> None:
        from Xlib import error as xerror

        root, windows = self._snapshot(conn)
        plan = plan_fit(root, windows)
        if not plan:
            return
        for win_id, (width, height) in plan:
            conn.create_resource_object("window", win_id).configure(
                x=0, y=0, width=width, height=height, border_width=0,
                # 窗口可能在快照后就被销毁；这类异步错误吃掉，下一轮自然收敛。
                onerror=xerror.CatchError(xerror.BadWindow, xerror.BadMatch),
            )
        conn.sync()
