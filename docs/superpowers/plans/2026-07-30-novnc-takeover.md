# noVNC 免密登录接管 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在账号列表挑一个已存 `sessionKey` 的账号，后台开一个注入好 Cookie、已登录 claude.ai 的 Camoufox 浏览器，用户经面板内的 noVNC 网页实时接管操作。

**Architecture:** 新增单例 `TakeoverManager`（`server/takeover.py`），管理一块独立的 Xvfb `:100` + headless=False 的 Camoufox + x11vnc（5900，绑 localhost），与注册流程各用各的屏、互不阻塞。noVNC 前端静态资源和 WebSocket 桥都经面板 8790 的鉴权路由暴露：静态走鉴权文件路由，WS `/vnc/websockify` 由 FastAPI 端点直接把帧对拷到 `127.0.0.1:5900`。不新开对外端口。

**Tech Stack:** Python 3.13 / FastAPI / sse-starlette / Camoufox(playwright-firefox) / x11vnc / noVNC 静态 / React(Vite) / pytest。包管理 `uv`（后端）与 `bun`（前端）。

---

## 背景速览（实现者必读）

- 后端结构：`server/app.py`(路由/鉴权/SSE)、`server/deps.py`(`AppState` 装配 DB/Config/Runner)、`server/runner.py`(注册后台任务)、`server/config_store.py`(`config.yaml` 读写 + 脱敏)、`server/db.py`、`server/auth.py`(HMAC cookie)。
- 浏览器：`claude_register/browser.py` 的 `browser_session()` 上下文管理器封装 Camoufox 启动（含代理/中继/geoip/指纹）。`pick_headless()` 在 Linux 有 Xvfb 时返回 `"virtual"`（camoufox 自选一块看不见的屏，外部无法定位）——所以接管必须自己管一块固定 `:100`。
- 鉴权：`auth.verify_token(token, password, secret)`；cookie 名 `auth.COOKIE_NAME = "cr_session"`。数据/动作路由用 `Depends(require_auth)`（空密码或无有效 cookie → 401）。
- 静态托管不能用裸 `StaticFiles`（mount 不继承 `Depends`）——现有 `/runs/{run_id}/{filename}` 是「鉴权 + 防路径穿越 + `FileResponse`」的范例，noVNC 静态照抄。
- 账号在 DB：`db.list_accounts(conn)` 返回全部账号 dict，含 `session_key`、`proxy` 字段。无「按 email 单查」函数，需要新增或用 list 过滤。
- 测试风格：`tests/` 用 pytest + `fastapi.testclient.TestClient` + monkeypatch；注册相关用可注入 `flow_fn`。后台任务测试用轮询等待（见 `tests/test_runner.py`、`tests/test_app.py`）。
- 运行测试：`uv run pytest tests/... -v`。

## File Structure

**Create:**
- `server/takeover.py` — `TakeoverManager`（单例生命周期）、`ProcessLauncher`（可注入子进程接口）、`open_takeover_browser`（真实浏览器句柄工厂）、异常 `TakeoverBusy`/`TakeoverError`。一个文件一件事：管理接管会话。
- `tests/test_takeover.py` — `TakeoverManager` 单元测试（注入假 launcher / 假 browser_fn）。
- `tests/test_takeover_api.py` — takeover REST 路由 + `/vnc/*` + WS 鉴权的集成测试。

**Modify:**
- `server/config_store.py` — `Config` 加 `takeover_enabled`、`takeover_idle_timeout_min`；`load_config`/`_FIELD_MAP` 同步。
- `claude_register/browser.py` — 抽出 `build_camoufox_kwargs(proxy)`；`browser_session` 改用它；新增 `open_takeover_browser` 迁到 `server/takeover.py`（用 browser.py 的 `build_camoufox_kwargs`）。
- `server/deps.py` — `AppState` 装配 `self.takeover = TakeoverManager(...)`。
- `server/app.py` — 挂 `/api/takeover/*`、`/vnc/{path}`、WS `/vnc/websockify`。
- `Dockerfile` — `apt-get install` 增加 `x11vnc`、`novnc`。
- `config.example.yaml` — 增加 `takeover:` 段示例。
- `web/src/api.js` — `takeoverStart/takeoverStop/takeoverStatus`。
- `web/src/pages/Dashboard.jsx` — 账号「接管」按钮 + 状态区。
- `README.md` — 接管功能说明。

**不改：** `docker-compose.yml`（端口不变）、`server/db.py` schema（无变更；仅新增一个纯读辅助函数 `get_account`）。

---

## Task 1: 配置项 takeover_enabled / takeover_idle_timeout_min

**Files:**
- Modify: `server/config_store.py`
- Test: `tests/test_config_store.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_config_store.py` 末尾追加：

```python
def test_takeover_defaults(tmp_path):
    from server.config_store import load_config
    cfg = load_config(tmp_path / "nope.yaml")  # 文件不存在→默认值
    assert cfg.takeover_enabled is True
    assert cfg.takeover_idle_timeout_min == 15


def test_takeover_roundtrip(tmp_path):
    from server.config_store import load_config, save_config
    p = tmp_path / "config.yaml"
    save_config(p, {"takeover_enabled": False, "takeover_idle_timeout_min": 30})
    cfg = load_config(p)
    assert cfg.takeover_enabled is False
    assert cfg.takeover_idle_timeout_min == 30
    # 落盘结构在 takeover 段
    import yaml
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert raw["takeover"]["enabled"] is False
    assert raw["takeover"]["idle_timeout_min"] == 30
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_config_store.py::test_takeover_defaults tests/test_config_store.py::test_takeover_roundtrip -v`
Expected: FAIL（`Config` 无 `takeover_enabled` 属性 / AttributeError）

- [ ] **Step 3: 最小实现**

在 `server/config_store.py` 的 `Config` dataclass 末尾加两字段：

```python
    register_proxy: str = ""
    takeover_enabled: bool = True
    takeover_idle_timeout_min: int = 15
```

在 `load_config` 里，`reg = raw.get("register", {}) or {}` 之后加：

```python
    tk = raw.get("takeover", {}) or {}
```

并在 `return Config(...)` 参数末尾加：

```python
        register_proxy=str(reg.get("proxy", "") or ""),
        takeover_enabled=bool(tk.get("enabled", True)),
        takeover_idle_timeout_min=int(tk.get("idle_timeout_min", 15)),
```

在 `_FIELD_MAP` 末尾加：

```python
    "register_proxy": ("register", "proxy"),
    "takeover_enabled": ("takeover", "enabled"),
    "takeover_idle_timeout_min": ("takeover", "idle_timeout_min"),
```

在 `save_config` 里 `out: dict = {"panel": {}, "anymail": {}, "register": {}}` 改为：

```python
    out: dict = {"panel": {}, "anymail": {}, "register": {}, "takeover": {}}
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_config_store.py -v`
Expected: PASS（含既有用例不回归）

- [ ] **Step 5: 提交**

```bash
git add server/config_store.py tests/test_config_store.py
git commit -m "feat(config): 新增 takeover 配置段(enabled/idle_timeout_min)"
```

---

## Task 2: 抽出 build_camoufox_kwargs（浏览器代理/指纹逻辑复用）

**Files:**
- Modify: `claude_register/browser.py`（`browser_session` 内联逻辑抽成函数）
- Test: `tests/test_proxy.py`

**说明：** 现 `browser_session()` 从 `proxy_cfg = parse_proxy(proxy)` 到 `kwargs["proxy"] = ...` 这段（解析代理→需要时起 SocksRelay→定 geoip→组 kwargs）要被接管会话复用。抽成 `build_camoufox_kwargs(proxy) -> (kwargs, relay, geoip)`，`browser_session` 改调它。行为保持不变。

- [ ] **Step 1: 写失败测试**

在 `tests/test_proxy.py` 末尾追加（无代理与无认证代理两条路径，不真起中继）：

```python
def test_build_kwargs_no_proxy():
    from claude_register.browser import build_camoufox_kwargs
    kwargs, relay, geoip = build_camoufox_kwargs(None)
    assert "proxy" not in kwargs
    assert relay is None
    assert geoip is True


def test_build_kwargs_plain_proxy():
    from claude_register.browser import build_camoufox_kwargs
    kwargs, relay, geoip = build_camoufox_kwargs("http://1.2.3.4:8080")
    assert kwargs["proxy"] == {"server": "http://1.2.3.4:8080"}
    assert relay is None          # 无认证不需要中继
    assert geoip is True
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_proxy.py::test_build_kwargs_no_proxy tests/test_proxy.py::test_build_kwargs_plain_proxy -v`
Expected: FAIL（`ImportError: cannot import name 'build_camoufox_kwargs'`）

- [ ] **Step 3: 重构实现**

在 `claude_register/browser.py` 中，`browser_session` 上方新增函数（把原 `browser_session` 里从 `proxy_cfg = parse_proxy(proxy)` 到 `else: ... log(...)` 的整段搬进来，末尾 `return`）：

```python
def build_camoufox_kwargs(proxy: str | None) -> tuple[dict, "SocksRelay | None", str | bool]:
    """把代理配置转成 Camoufox 的 proxy kwargs，并决定 geoip。

    注册会话与接管会话共用这段：解析代理 → 带认证 SOCKS5 起本地中继 →
    用出口 IP 对齐 geoip → 组 kwargs。返回 (kwargs, relay, geoip)，
    relay 需由调用方在会话结束时 stop()。
    """
    proxy_cfg = parse_proxy(proxy)
    relay = None
    kwargs: dict = {}
    geoip: str | bool = True
    if proxy_cfg is not None:
        if needs_relay(proxy_cfg):
            relay_sink = current_sink()

            def _relay_log(msg: str) -> None:
                relay_sink(f"代理中继：{msg}")

            try:
                relay = SocksRelay(
                    normalize_proxy_url(proxy),
                    on_error=_relay_log,
                ).start()
            except Exception as exc:
                raise RuntimeError(
                    f"启动本地代理中继失败（{exc}）。请检查代理地址 "
                    f"{proxy_cfg['server']} 是否可达。"
                ) from exc
            kwargs["proxy"] = {"server": relay.local_url}
            log(f"使用代理：{proxy_cfg['server']}（带认证，经本地中继 {relay.local_url}）")
            exit_ip = relay.exit_ip()
            if exit_ip:
                geoip = exit_ip
                log(f"代理出口 IP：{exit_ip}")
            else:
                log("查不到代理出口 IP，交给 camoufox 自行探测。")
        else:
            kwargs["proxy"] = proxy_cfg
            log(f"使用代理：{proxy_cfg['server']}")
    return kwargs, relay, geoip
```

然后把 `browser_session` 里那段替换为调用：

```python
@contextmanager
def browser_session(proxy: str | None = None):
    """...(docstring 保留原文)..."""
    kwargs, relay, geoip = build_camoufox_kwargs(proxy)
    headless = pick_headless()
    cm = Camoufox(
        headless=headless,
        humanize=True,
        locale="en-US",
        geoip=geoip,
        window=(1280, 900),
        **kwargs,
    )
    try:
        browser = cm.__enter__()
    except Exception as exc:
        if relay is not None:
            relay.stop()
        extra = "，并确认已安装 Xvfb" if headless == "virtual" else ""
        raise RuntimeError(
            f"启动 Camoufox 失败（{exc}）。请先运行 `uv run camoufox fetch` "
            f"下载浏览器二进制{extra}。"
        ) from exc
    log(f"已启动 Camoufox（headless={headless}）")
    try:
        yield browser
    finally:
        cm.__exit__(None, None, None)
        if relay is not None:
            relay.stop()
```

- [ ] **Step 4: 运行确认通过 + 无回归**

Run: `uv run pytest tests/test_proxy.py tests/test_relay_throttle.py tests/test_run_browser_close.py -v`
Expected: PASS（新用例通过，既有浏览器/代理用例不回归）

- [ ] **Step 5: 提交**

```bash
git add claude_register/browser.py tests/test_proxy.py
git commit -m "refactor(browser): 抽出 build_camoufox_kwargs 供接管会话复用"
```

---

## Task 3: TakeoverManager 生命周期（注入 launcher + browser_fn）

**Files:**
- Create: `server/takeover.py`
- Test: `tests/test_takeover.py`

**接口约定（后续任务依赖，务必一致）：**

```python
class TakeoverBusy(Exception): ...          # 已有会话在跑
class TakeoverError(Exception): ...          # 启动过程失败（已回滚）

class ProcessLauncher:
    def spawn(self, argv: list[str]): ...     # 返回带 .terminate()/.kill()/.poll() 的对象；默认用 subprocess.Popen

class TakeoverManager:
    def __init__(self, *, now_fn, launcher=None, browser_fn=None,
                 wait_display_fn=None, display=":100", vnc_port=5900): ...
    def start(self, *, email: str, session_key: str, proxy: str = "",
              idle_timeout_s: float = 900) -> dict: ...   # {"email","started_at"}；忙→TakeoverBusy；失败→TakeoverError（已回滚）
    def stop(self) -> None: ...               # 幂等：没在跑也不报错
    def status(self) -> dict: ...             # {"running": bool, "email": str|None, "started_at": str|None}
```

- `launcher` 默认 `ProcessLauncher()`；`browser_fn(session_key, proxy, display) -> handle`，handle 有 `.close()`；默认在 Task 4 接真实现。
- `wait_display_fn(display) -> None` 默认轮询 X socket（Task 4）；测试注入 no-op。
- 启动顺序：Xvfb → wait_display → browser_fn → x11vnc；`idle_timeout_s` 用 `threading.Timer` 到点自动 `stop()`。
- 任一步抛异常：回滚已起的（反序 terminate 子进程 / `handle.close()`），抛 `TakeoverError`。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_takeover.py`：

```python
import threading
import pytest
from server.takeover import TakeoverManager, TakeoverBusy, TakeoverError


def _now():
    return "2026-07-30T00:00:00Z"


class FakeProc:
    def __init__(self, argv):
        self.argv = argv
        self.terminated = False
    def poll(self):
        return None
    def terminate(self):
        self.terminated = True
    def kill(self):
        self.terminated = True
    def wait(self, timeout=None):
        return 0


class FakeLauncher:
    def __init__(self):
        self.spawned = []
    def spawn(self, argv):
        p = FakeProc(argv)
        self.spawned.append(p)
        return p


class FakeBrowser:
    def __init__(self):
        self.closed = False
    def close(self):
        self.closed = True


def _mgr(**kw):
    return TakeoverManager(
        now_fn=_now,
        launcher=kw.pop("launcher", FakeLauncher()),
        browser_fn=kw.pop("browser_fn", lambda **k: FakeBrowser()),
        wait_display_fn=kw.pop("wait_display_fn", lambda display: None),
        **kw,
    )


def test_start_status_stop_sequence():
    launcher = FakeLauncher()
    browsers = []
    def bf(**k):
        b = FakeBrowser(); browsers.append(b); return b
    m = _mgr(launcher=launcher, browser_fn=bf)

    info = m.start(email="a@x.com", session_key="sk", proxy="", idle_timeout_s=999)
    assert info["email"] == "a@x.com"
    assert m.status() == {"running": True, "email": "a@x.com", "started_at": "2026-07-30T00:00:00Z"}
    # 起了 Xvfb 和 x11vnc 两个子进程 + 一个浏览器
    argv0 = [p.argv[0] for p in launcher.spawned]
    assert "Xvfb" in argv0 and "x11vnc" in argv0
    assert len(browsers) == 1

    m.stop()
    assert m.status()["running"] is False
    assert browsers[0].closed is True
    assert all(p.terminated for p in launcher.spawned)


def test_second_start_while_running_raises_busy():
    m = _mgr()
    m.start(email="a@x.com", session_key="sk", idle_timeout_s=999)
    with pytest.raises(TakeoverBusy):
        m.start(email="b@x.com", session_key="sk2", idle_timeout_s=999)
    m.stop()


def test_start_rolls_back_when_browser_fails():
    launcher = FakeLauncher()
    def bad_browser(**k):
        raise RuntimeError("boom")
    m = _mgr(launcher=launcher, browser_fn=bad_browser)
    with pytest.raises(TakeoverError):
        m.start(email="a@x.com", session_key="sk", idle_timeout_s=999)
    # 浏览器失败前起的 Xvfb 必须被回滚
    assert all(p.terminated for p in launcher.spawned)
    assert m.status()["running"] is False


def test_stop_is_idempotent():
    m = _mgr()
    m.stop()  # 没在跑也不报错
    assert m.status()["running"] is False


def test_idle_timeout_auto_stops():
    m = _mgr()
    m.start(email="a@x.com", session_key="sk", idle_timeout_s=0.05)
    # 到点自动 stop
    for _ in range(50):
        if not m.status()["running"]:
            break
        threading.Event().wait(0.02)
    assert m.status()["running"] is False
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_takeover.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'server.takeover'`）

- [ ] **Step 3: 实现 TakeoverManager**

创建 `server/takeover.py`：

```python
"""接管会话：注入 sessionKey 的已登录 claude.ai 浏览器 + x11vnc，供 noVNC 接管。

与注册流程（server/runner.py）平级、各用各的屏：注册走 Camoufox 的 "virtual"
自选屏，接管自己管一块固定的 Xvfb :100，x11vnc 精确挂上去。单例：同一时刻
只允许一个接管会话。全部子进程只绑 localhost，不对外暴露端口。
"""
from __future__ import annotations

import subprocess
import threading

from claude_register import console


class TakeoverBusy(Exception):
    pass


class TakeoverError(Exception):
    pass


class ProcessLauncher:
    """subprocess.Popen 的薄封装，便于测试注入假实现。"""

    def spawn(self, argv: list[str]):
        return subprocess.Popen(argv)


def _terminate(proc) -> None:
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


class TakeoverManager:
    def __init__(self, *, now_fn, launcher=None, browser_fn=None,
                 wait_display_fn=None, display=":100", vnc_port=5900):
        self.now_fn = now_fn
        self.launcher = launcher or ProcessLauncher()
        self._browser_fn = browser_fn
        self._wait_display = wait_display_fn or (lambda display: None)
        self.display = display
        self.vnc_port = vnc_port
        self._lock = threading.RLock()
        self._active = False
        self._email = None
        self._started_at = None
        self._xvfb = None
        self._x11vnc = None
        self._browser = None
        self._timer = None

    # ---- 查询 ----
    def status(self) -> dict:
        with self._lock:
            return {
                "running": self._active,
                "email": self._email,
                "started_at": self._started_at,
            }

    # ---- 启动 ----
    def start(self, *, email, session_key, proxy="", idle_timeout_s=900) -> dict:
        with self._lock:
            if self._active:
                raise TakeoverBusy("已有接管会话，请先结束")
            if self._browser_fn is None:
                from server.takeover_browser import open_takeover_browser
                self._browser_fn = open_takeover_browser
            try:
                self._xvfb = self.launcher.spawn([
                    "Xvfb", self.display, "-screen", "0", "1280x900x24",
                    "-nolisten", "tcp",
                ])
                self._wait_display(self.display)
                self._browser = self._browser_fn(
                    session_key=session_key, proxy=proxy, display=self.display,
                )
                self._x11vnc = self.launcher.spawn([
                    "x11vnc", "-display", self.display, "-localhost", "-forever",
                    "-shared", "-rfbport", str(self.vnc_port), "-nopw", "-quiet",
                ])
            except Exception as exc:  # noqa: BLE001
                self._teardown()
                raise TakeoverError(f"启动接管会话失败：{exc}") from exc
            self._active = True
            self._email = email
            self._started_at = self.now_fn()
            console.log(f"接管会话已启动：{email}")
            self._timer = threading.Timer(idle_timeout_s, self._idle_stop)
            self._timer.daemon = True
            self._timer.start()
            return {"email": email, "started_at": self._started_at}

    def _idle_stop(self):
        console.log("接管会话空闲超时，自动结束。")
        self.stop()

    # ---- 结束 ----
    def stop(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._teardown()
            self._active = False
            self._email = None
            self._started_at = None

    def _teardown(self):
        # 反序：x11vnc → 浏览器 → Xvfb
        if self._x11vnc is not None:
            _terminate(self._x11vnc)
            self._x11vnc = None
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._xvfb is not None:
            _terminate(self._xvfb)
            self._xvfb = None
```

> 注意：`browser_fn` 用关键字参数 `session_key/proxy/display` 调用，测试的 `lambda **k:` 与之匹配。默认实现从 `server.takeover_browser` 延迟导入（Task 4 创建），避免测试环境导入 Camoufox。

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_takeover.py -v`
Expected: PASS（5 条全绿）

- [ ] **Step 5: 提交**

```bash
git add server/takeover.py tests/test_takeover.py
git commit -m "feat(takeover): TakeoverManager 单例生命周期(start/stop/status/超时/回滚)"
```

---

## Task 4: 真实浏览器句柄 + X socket 等待

**Files:**
- Create: `server/takeover_browser.py`
- Test: `tests/test_takeover.py`（加两条针对 `wait_x_socket` 的用例）

**说明：** Task 3 的 `browser_fn` / `wait_display_fn` 默认实现放这里，隔离对 Camoufox/文件系统的依赖。`open_takeover_browser` 用 Task 2 的 `build_camoufox_kwargs`，以 `headless=False` + `env={"DISPLAY": display}` 启动 Camoufox，注入 sessionKey 后访问 claude.ai，返回带 `.close()` 的句柄。

- [ ] **Step 1: 写失败测试**

在 `tests/test_takeover.py` 末尾追加：

```python
def test_wait_x_socket_times_out_fast(tmp_path):
    from server.takeover_browser import wait_x_socket
    import pytest
    # 指向一个不存在的 socket 目录，超时应快速抛错而不是卡住
    with pytest.raises(TimeoutError):
        wait_x_socket(":100", timeout=0.2, poll=0.02, sock_dir=str(tmp_path))


def test_wait_x_socket_succeeds_when_present(tmp_path):
    from server.takeover_browser import wait_x_socket
    (tmp_path / "X100").write_text("")  # 伪造 X socket 文件
    wait_x_socket(":100", timeout=0.5, poll=0.02, sock_dir=str(tmp_path))  # 不抛即通过
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_takeover.py::test_wait_x_socket_times_out_fast tests/test_takeover.py::test_wait_x_socket_succeeds_when_present -v`
Expected: FAIL（`No module named 'server.takeover_browser'`）

- [ ] **Step 3: 实现**

创建 `server/takeover_browser.py`：

```python
"""接管会话的真实副作用实现：等 X socket 就绪、开注入 Cookie 的 Camoufox。

与 server/takeover.py 分开，好让 TakeoverManager 单测完全不碰 Camoufox / 文件系统。
"""
from __future__ import annotations

import os
import time

from camoufox.sync_api import Camoufox

from claude_register.browser import build_camoufox_kwargs
from claude_register.console import log


def wait_x_socket(display: str, timeout: float = 10.0, poll: float = 0.1,
                  sock_dir: str = "/tmp/.X11-unix") -> None:
    """轮询等待 Xvfb 的 UNIX socket 出现（display ":100" → 文件 X100）。"""
    num = display.lstrip(":").split(".")[0]
    path = os.path.join(sock_dir, f"X{num}")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.exists(path):
            return
        time.sleep(poll)
    raise TimeoutError(f"等待 X 显示 {display} 就绪超时（{path} 未出现）")


class _BrowserHandle:
    def __init__(self, cm, relay):
        self._cm = cm
        self._relay = relay

    def close(self):
        try:
            self._cm.__exit__(None, None, None)
        finally:
            if self._relay is not None:
                self._relay.stop()


def open_takeover_browser(*, session_key: str, proxy: str = "", display: str = ":100"):
    """开一个已登录 claude.ai 的 Camoufox（挂在指定 X display 上），返回带 .close() 的句柄。"""
    kwargs, relay, geoip = build_camoufox_kwargs(proxy or None)
    cm = Camoufox(
        headless=False,
        humanize=True,
        locale="en-US",
        geoip=geoip,
        window=(1280, 900),
        env={"DISPLAY": display},
        **kwargs,
    )
    try:
        browser = cm.__enter__()
    except Exception as exc:
        if relay is not None:
            relay.stop()
        raise RuntimeError(
            f"启动接管浏览器失败（{exc}）。请确认已 `uv run camoufox fetch` 且 Xvfb 可用。"
        ) from exc
    try:
        context = browser.new_context(no_viewport=True)
        context.add_cookies([{
            "name": "sessionKey",
            "value": session_key,
            "domain": ".claude.ai",
            "path": "/",
            "secure": True,
            "httpOnly": True,
        }])
        page = context.new_page()
        page.goto("https://claude.ai", wait_until="domcontentloaded", timeout=60_000)
        log("接管浏览器已注入 sessionKey 并打开 claude.ai。")
    except Exception as exc:
        cm.__exit__(None, None, None)
        if relay is not None:
            relay.stop()
        raise RuntimeError(f"注入 sessionKey / 打开 claude.ai 失败（{exc}）。") from exc
    return _BrowserHandle(cm, relay)
```

并把 Task 3 里 `TakeoverManager.start` 中的默认 `wait_display_fn` 接上真实现——修改 `server/takeover.py` 的 `__init__`：

```python
        if wait_display_fn is not None:
            self._wait_display = wait_display_fn
        else:
            from server.takeover_browser import wait_x_socket
            self._wait_display = lambda display: wait_x_socket(display)
```

（替换原来的 `self._wait_display = wait_display_fn or (lambda display: None)`。）

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_takeover.py -v`
Expected: PASS（含新加的 socket 用例；旧用例因显式注入 `wait_display_fn` 不受影响）

- [ ] **Step 5: 提交**

```bash
git add server/takeover_browser.py server/takeover.py
git commit -m "feat(takeover): 真实浏览器句柄 open_takeover_browser + wait_x_socket"
```

---

## Task 5: AppState 装配 + takeover REST 路由

**Files:**
- Modify: `server/deps.py`、`server/db.py`（加 `get_account`）、`server/app.py`
- Test: `tests/test_takeover_api.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_takeover_api.py`：

```python
from fastapi.testclient import TestClient
from server.app import create_app
from server.config_store import save_config
from server import db


def _client(tmp_path, monkeypatch):
    # 注入假 TakeoverManager，避免真起 Xvfb/浏览器
    import server.deps as deps

    class FakeMgr:
        def __init__(self, *a, **k):
            self._running = False
            self._email = None
        def start(self, *, email, session_key, proxy="", idle_timeout_s=900):
            from server.takeover import TakeoverBusy
            if self._running:
                raise TakeoverBusy("busy")
            self._running = True; self._email = email
            return {"email": email, "started_at": "2026-07-30T00:00:00Z"}
        def stop(self):
            self._running = False; self._email = None
        def status(self):
            return {"running": self._running, "email": self._email,
                    "started_at": "2026-07-30T00:00:00Z" if self._running else None}

    monkeypatch.setattr(deps, "TakeoverManager", FakeMgr)
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml",
                     now_fn=lambda: "2026-07-30T00:00:00Z")
    return app


def _login(c):
    assert c.post("/api/login", json={"password": "pw"}).status_code == 200


def _seed_account(tmp_path, email, session_key="sk", proxy=""):
    conn = db.init_db(tmp_path / "claude-register.db")
    db.upsert_account(conn, email, "x.com", None, "", None, "success",
                      session_key=session_key, proxy=proxy, created_at="t")
    conn.commit()


def test_takeover_requires_auth(tmp_path, monkeypatch):
    save_config(tmp_path / "config.yaml", {"panel_password": "pw"})
    app = _client(tmp_path, monkeypatch)
    c = TestClient(app)
    assert c.get("/api/takeover").status_code == 401
    assert c.post("/api/takeover/start", json={"email": "a@x.com"}).status_code == 401


def test_takeover_start_no_session_key_400(tmp_path, monkeypatch):
    save_config(tmp_path / "config.yaml", {"panel_password": "pw"})
    _seed_account(tmp_path, "a@x.com", session_key="")
    app = _client(tmp_path, monkeypatch)
    c = TestClient(app); _login(c)
    assert c.post("/api/takeover/start", json={"email": "a@x.com"}).status_code == 400


def test_takeover_start_unknown_account_404(tmp_path, monkeypatch):
    save_config(tmp_path / "config.yaml", {"panel_password": "pw"})
    app = _client(tmp_path, monkeypatch)
    c = TestClient(app); _login(c)
    assert c.post("/api/takeover/start", json={"email": "nobody@x.com"}).status_code == 404


def test_takeover_disabled_403(tmp_path, monkeypatch):
    save_config(tmp_path / "config.yaml", {"panel_password": "pw", "takeover_enabled": False})
    _seed_account(tmp_path, "a@x.com")
    app = _client(tmp_path, monkeypatch)
    c = TestClient(app); _login(c)
    assert c.post("/api/takeover/start", json={"email": "a@x.com"}).status_code == 403


def test_takeover_start_stop_status_flow(tmp_path, monkeypatch):
    save_config(tmp_path / "config.yaml", {"panel_password": "pw"})
    _seed_account(tmp_path, "a@x.com", session_key="sk", proxy="socks5://p:1")
    app = _client(tmp_path, monkeypatch)
    c = TestClient(app); _login(c)
    r = c.post("/api/takeover/start", json={"email": "a@x.com"})
    assert r.status_code == 200 and r.json()["email"] == "a@x.com"
    assert c.get("/api/takeover").json()["running"] is True
    # 再启动一次 → 409（busy）
    assert c.post("/api/takeover/start", json={"email": "a@x.com"}).status_code == 409
    assert c.post("/api/takeover/stop").status_code == 200
    assert c.get("/api/takeover").json()["running"] is False
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_takeover_api.py -v`
Expected: FAIL（路由不存在 → 404/401 断言不符，或 `AttributeError: state.takeover`）

- [ ] **Step 3a: db.get_account**

在 `server/db.py` 的 `list_accounts` 下方新增：

```python
def get_account(conn, email) -> dict | None:
    row = conn.execute("SELECT * FROM accounts WHERE email=?", (email,)).fetchone()
    return dict(row) if row else None
```

- [ ] **Step 3b: AppState 装配**

在 `server/deps.py` 顶部 import 加：

```python
from server.takeover import TakeoverManager
```

在 `AppState.__init__` 末尾（`self.secret` 之后）加：

```python
        self.takeover = TakeoverManager(now_fn=now_fn)
```

- [ ] **Step 3c: 路由**

在 `server/app.py` 里 import 段加 `from server.takeover import TakeoverBusy`（`db` 已 import）。在 `rerun` 路由之后、`runs_dir` 之前插入：

```python
    @app.post("/api/takeover/start")
    async def takeover_start(request: Request, _=Depends(require_auth)):
        cfg = state.config()
        if not cfg.takeover_enabled:
            raise HTTPException(status_code=403, detail="接管功能已禁用")
        body = await request.json()
        email = str(body.get("email", "") or "")
        row = db.get_account(state.conn, email)
        if row is None:
            raise HTTPException(status_code=404, detail="账号不存在")
        if not row.get("session_key"):
            raise HTTPException(status_code=400, detail="该账号无 sessionKey")
        try:
            info = state.takeover.start(
                email=email,
                session_key=row["session_key"],
                proxy=row.get("proxy") or "",
                idle_timeout_s=cfg.takeover_idle_timeout_min * 60,
            )
        except TakeoverBusy:
            raise HTTPException(status_code=409, detail="已有接管会话，请先结束")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"启动接管失败：{exc}")
        return info

    @app.post("/api/takeover/stop")
    def takeover_stop(_=Depends(require_auth)):
        state.takeover.stop()
        return {"ok": True}

    @app.get("/api/takeover")
    def takeover_status(_=Depends(require_auth)):
        return state.takeover.status()
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_takeover_api.py -v`
Expected: PASS（6 条全绿）

- [ ] **Step 5: 提交**

```bash
git add server/deps.py server/db.py server/app.py tests/test_takeover_api.py
git commit -m "feat(takeover): AppState 装配 + /api/takeover start/stop/status 路由"
```

---

## Task 6: noVNC 静态路由 + WebSocket 桥（鉴权）

**Files:**
- Modify: `server/app.py`
- Test: `tests/test_takeover_api.py`（追加静态与 WS 鉴权用例）

**说明：** noVNC 静态资源目录由环境变量 `NOVNC_DIR`（默认 `/usr/share/novnc`）指定，测试用 `monkeypatch.setenv` 指向临时目录。WS `/vnc/websockify` 先校验 cookie，不通过 `close(1008)`；通过后开一条到 `127.0.0.1:5900` 的 asyncio TCP 连接双向对拷。

- [ ] **Step 1: 写失败测试**

在 `tests/test_takeover_api.py` 末尾追加：

```python
import os


def test_vnc_static_requires_auth(tmp_path, monkeypatch):
    save_config(tmp_path / "config.yaml", {"panel_password": "pw"})
    novnc = tmp_path / "novnc"; novnc.mkdir()
    (novnc / "vnc.html").write_text("<html>novnc</html>")
    monkeypatch.setenv("NOVNC_DIR", str(novnc))
    app = _client(tmp_path, monkeypatch)
    c = TestClient(app)
    # 未登录 → 401
    assert c.get("/vnc/vnc.html").status_code == 401
    _login(c)
    r = c.get("/vnc/vnc.html")
    assert r.status_code == 200 and "novnc" in r.text


def test_vnc_static_blocks_traversal(tmp_path, monkeypatch):
    save_config(tmp_path / "config.yaml", {"panel_password": "pw"})
    novnc = tmp_path / "novnc"; novnc.mkdir()
    (novnc / "vnc.html").write_text("ok")
    monkeypatch.setenv("NOVNC_DIR", str(novnc))
    app = _client(tmp_path, monkeypatch)
    c = TestClient(app); _login(c)
    assert c.get("/vnc/../../etc/passwd").status_code == 404


def test_vnc_ws_rejects_without_cookie(tmp_path, monkeypatch):
    import pytest
    from starlette.websockets import WebSocketDisconnect
    save_config(tmp_path / "config.yaml", {"panel_password": "pw"})
    app = _client(tmp_path, monkeypatch)
    c = TestClient(app)
    # 无 cookie 连 WS：服务端应拒绝（握手后立即 close(1008)）
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/vnc/websockify") as ws:
            ws.receive_bytes()
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_takeover_api.py::test_vnc_static_requires_auth tests/test_takeover_api.py::test_vnc_ws_rejects_without_cookie -v`
Expected: FAIL（路由不存在）

- [ ] **Step 3: 实现**

在 `server/app.py` import 段加：

```python
import asyncio
import os
from fastapi import WebSocket
```

在 Task 5 的 takeover 路由之后插入静态与 WS：

```python
    NOVNC_DIR = os.environ.get("NOVNC_DIR", "/usr/share/novnc")

    @app.get("/vnc/{path:path}")
    def vnc_static(path: str, _=Depends(require_auth)):
        base = Path(NOVNC_DIR).resolve()
        target = (base / (path or "vnc.html")).resolve()
        if base != target and base not in target.parents:
            raise HTTPException(status_code=404)
        if not target.is_file():
            raise HTTPException(status_code=404)
        return FileResponse(target)

    @app.websocket("/vnc/websockify")
    async def vnc_ws(ws: WebSocket):
        cfg = state.config()
        token = ws.cookies.get(auth.COOKIE_NAME, "")
        if not cfg.panel_password or not auth.verify_token(token, cfg.panel_password, state.secret):
            await ws.close(code=1008)
            return
        await ws.accept(subprotocol="binary")
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", 5900)
        except Exception:
            await ws.close(code=1011)
            return

        async def ws_to_tcp():
            try:
                while True:
                    data = await ws.receive_bytes()
                    writer.write(data)
                    await writer.drain()
            except Exception:
                pass
            finally:
                writer.close()

        async def tcp_to_ws():
            try:
                while True:
                    data = await reader.read(65536)
                    if not data:
                        break
                    await ws.send_bytes(data)
            except Exception:
                pass

        await asyncio.gather(ws_to_tcp(), tcp_to_ws())
```

> `auth` 已在 `server/app.py` 顶部 import（现有 `from server import auth, db`）。`Path`、`FileResponse`、`Depends`、`HTTPException` 均已 import。

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_takeover_api.py -v`
Expected: PASS（含静态 401/200、防穿越 404、WS 无 cookie 被拒）

- [ ] **Step 5: 提交**

```bash
git add server/app.py tests/test_takeover_api.py
git commit -m "feat(takeover): noVNC 鉴权静态路由 + /vnc/websockify WS 桥接 5900"
```

---

## Task 7: Dockerfile 装 x11vnc + novnc

**Files:**
- Modify: `Dockerfile`

**说明：** 运行镜像的 `apt-get install` 列表里已有 `xvfb`。加 `x11vnc` 与 `novnc`（novnc 提供 `/usr/share/novnc` 静态前端；它依赖会带上 `python3-websockify`，但我们不用它的进程）。无法在 CI 里跑真 X，故本任务只做构建面的改动 + 人工验证清单。

- [ ] **Step 1: 改 Dockerfile**

在运行镜像的 `apt-get install -y --no-install-recommends \` 列表中，`xvfb \` 之后加两行：

```dockerfile
    xvfb \
    x11vnc \
    novnc \
```

- [ ] **Step 2: 本地静态语法自检（不构建镜像）**

Run: `grep -n "x11vnc\|novnc" Dockerfile`
Expected: 能看到新增的 `x11vnc` 与 `novnc` 两行。

> 线上不跑 `docker build`（见 CLAUDE.md 操作规约）；镜像由 CI（self-hosted runner）构建。真实验证在 Task 9 的人工清单里。

- [ ] **Step 3: 提交**

```bash
git add Dockerfile
git commit -m "build(docker): 运行镜像装 x11vnc + novnc 供接管使用"
```

---

## Task 8: 前端「接管」按钮 + 状态区

**Files:**
- Modify: `web/src/api.js`、`web/src/pages/Dashboard.jsx`

**说明：** 前端无单测框架，按项目现状人工验证（`bun run build` 通过 + 后续人工点测）。账号有 `session_key` 时显示「接管」按钮；点击 `takeoverStart` 成功后 `window.open('/vnc/vnc.html?autoconnect=1&resize=scale&path=vnc/websockify')` 新标签接管。顶部显示当前接管状态 + 「结束接管」。

- [ ] **Step 1: api.js 增接口**

在 `web/src/api.js` 的 `export const api = {` 内，`rerun` 之后加：

```javascript
  takeoverStart: (email) =>
    fetch("/api/takeover/start", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ email }),
    }).then(j),

  takeoverStop: () =>
    fetch("/api/takeover/stop", { method: "POST" }).then(j),

  takeoverStatus: () => fetch("/api/takeover").then(j),
```

- [ ] **Step 2: Dashboard 状态 + 拉取**

在 `web/src/pages/Dashboard.jsx` 组件里，`const [accounts, setAccounts] = useState([]);` 附近加：

```javascript
  const [takeover, setTakeover] = useState({ running: false, email: null });
  const [takeoverError, setTakeoverError] = useState("");
```

在已有的加载 effect（`api.listAccounts().then(...)` 所在处）里补一行拉状态：

```javascript
    api.takeoverStatus().then(setTakeover).catch(() => {});
```

在组件内新增两个处理函数（放在 `exportAll` 之类函数附近）：

```javascript
  const startTakeover = async (email) => {
    setTakeoverError("");
    try {
      await api.takeoverStart(email);
      const st = await api.takeoverStatus();
      setTakeover(st);
      window.open(
        "/vnc/vnc.html?autoconnect=1&resize=scale&path=vnc/websockify",
        "_blank",
        "noopener",
      );
    } catch (e) {
      setTakeoverError(
        e.status === 409
          ? "已有接管会话，请先结束"
          : e.status === 400
            ? "该账号无 sessionKey"
            : e.status === 403
              ? "接管功能已禁用"
              : `启动接管失败（${e.status || "?"})`,
      );
    }
  };

  const stopTakeover = async () => {
    try {
      await api.takeoverStop();
      setTakeover({ running: false, email: null });
    } catch {
      /* ignore */
    }
  };
```

- [ ] **Step 3: 渲染按钮 + 状态区**

在账号列表标题区（`accounts.length > 0 && <button ... exportAll>` 附近）加一段接管状态条：

```jsx
      {takeover.running && (
        <div className="takeover-bar">
          正在接管：{takeover.email}
          <button className="btn btn-small" onClick={stopTakeover}>
            结束接管
          </button>
        </div>
      )}
      {takeoverError && <div className="error-msg">{takeoverError}</div>}
```

在每个账号行的按钮组里（`copyLine` 按钮附近），仅当有 sessionKey 时渲染：

```jsx
                {a.session_key && (
                  <button
                    className="btn btn-small"
                    onClick={() => startTakeover(a.email)}
                  >
                    接管
                  </button>
                )}
```

- [ ] **Step 4: 前端构建自检**

Run: `cd web && bun install && bun run build`
Expected: 构建成功，`web/dist` 生成，无报错。

- [ ] **Step 5: 提交**

```bash
git add web/src/api.js web/src/pages/Dashboard.jsx
git commit -m "feat(web): 账号列表接管按钮 + 接管状态条"
```

---

## Task 9: 文档 + config 示例 + 人工验证清单

**Files:**
- Modify: `config.example.yaml`、`README.md`

- [ ] **Step 1: config.example.yaml 增段**

在 `config.example.yaml` 末尾（照现有缩进风格）加：

```yaml
takeover:
  enabled: true          # 是否允许 noVNC 免密接管
  idle_timeout_min: 15   # 接管会话空闲多少分钟后自动结束
```

- [ ] **Step 2: README 增说明**

在 `README.md` 合适位置（账号/面板相关章节后）加一节：

```markdown
## noVNC 免密登录接管

账号列表里凡抓到 `sessionKey` 的账号，都可点「接管」：后台会用这份 Cookie 开一个
已登录 claude.ai 的浏览器，并通过面板内嵌的 noVNC 让你在网页上实时接管操作。

- 只经面板 8790 反代，复用面板密码鉴权，**不额外对外开端口**（x11vnc 仅绑 localhost）。
- 同一时刻只允许一个接管会话；默认空闲 15 分钟自动结束（`config.yaml` 的
  `takeover.idle_timeout_min` 可调，`takeover.enabled: false` 可整体关闭）。
- 接管会话用独立的虚拟屏（`:100`），与注册流程并行、互不影响。
- sessionKey 是会话级凭证，换环境或过期会失效，接管打不开时重新注册获取即可。
```

- [ ] **Step 3: 提交**

```bash
git add config.example.yaml README.md
git commit -m "docs(takeover): README 说明 + config.example 增 takeover 段"
```

- [ ] **Step 4: 全量测试回归**

Run: `uv run pytest -q`
Expected: 全绿（新增 takeover/config/proxy 用例 + 既有用例无回归）。

- [ ] **Step 5: 人工验证清单（部署后，非 CI）**

> 镜像经 CI 构建推送后，在部署机（LXC 10014，见记忆）`docker compose pull && up -d`，然后：

1. 面板登录 → 账号列表点一个有 sessionKey 账号的「接管」→ 新标签打开 noVNC。
2. noVNC 里能看到 Camoufox 窗口且已是 claude.ai 登录态，可鼠标键盘操作。
3. 顶部状态条显示「正在接管：<email>」；点「结束接管」→ 窗口关闭、状态清空。
4. 接管进行中并发触发一次注册，确认注册正常跑（`:99` 与 `:100` 不打架）。
5. 等 `idle_timeout_min` 分钟不操作，确认接管会话自动结束、进程无残留
   （`docker exec <容器> pgrep -a Xvfb; pgrep -a x11vnc` 应无 :100 相关残留）。
6. **关键验证**：确认 Camoufox 窗口确实落在 `:100`——若 noVNC 里是黑屏/无窗口，
   说明 `env={"DISPLAY": display}` 未被 Camoufox 透传，改为在 `open_takeover_browser`
   里用模块级锁临时设 `os.environ["DISPLAY"]` 兜底（注意与注册的 virtual 屏串行，
   避免 os.environ 竞争）。

---

## 自查记录

- **Spec 覆盖**：架构(Task 3/4/6)、TakeoverManager(3)、代理复用(2)、noVNC 反代(6)、Web UI(8)、配置段(1)、错误码 401/409/400/404/403/500(5)、并发独立屏(3+人工验证4)、安全 localhost/鉴权(6)、测试(3/5/6)、Dockerfile(7)、文档(9) —— 均有对应任务。
- **占位符**：无 TBD/TODO；每个代码步给出完整代码与命令。
- **类型/命名一致性**：`TakeoverManager.start/stop/status`、`TakeoverBusy/TakeoverError`、`build_camoufox_kwargs`、`open_takeover_browser`、`wait_x_socket`、`db.get_account`、路由 `/api/takeover/*` 与 `/vnc/*`、前端 `takeoverStart/Stop/Status` 在各任务间保持一致。
- **已知风险**：`env={"DISPLAY"}` 透传由 Task 9 人工步骤 6 验证并给出兜底方案。
