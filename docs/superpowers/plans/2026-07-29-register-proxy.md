# 注册代理支持 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 注册时浏览器（Camoufox）可经单个固定代理出口访问 claude.ai；留空直连；anymail 不走代理。

**Architecture:** 配置新增 `register.proxy`（代理 URL 字符串）→ `flow.run` 从 Config 读取 → `run_browser` / `browser_session` 透传 → 解析成 Playwright 风格 proxy dict 传给 `Camoufox(proxy=...)`。非法 URL 在启动阶段抛 `ValueError`，不静默直连。

**Tech Stack:** Python 3.13 + uv + pytest；Camoufox（Playwright 风格 proxy 参数）；React 前端（Settings.jsx）。

**Spec:** `docs/superpowers/specs/2026-07-29-register-proxy-design.md`

## Global Constraints

- 测试命令一律 `uv run pytest ...`；不跑任何打包/构建命令（用户规约：本地不构建）。
- 代理 URL 合法格式：`http://host:port`、`http://user:pass@host:port`、`socks5://host:port`（认证同理）。scheme、host、port 三者缺一不可。
- 非法代理 URL → `ValueError`（中文错误信息含格式示例），**不静默降级直连**。
- 日志打印代理时只打 server（scheme://host:port），不打印用户名/密码。
- `geoip=True` 保持不变。
- 提交信息结尾带 Happy/Claude 双署名（见仓库近期提交格式）。

---

### Task 1: `parse_proxy` 解析函数（browser.py）

**Files:**
- Modify: `claude_register/browser.py`（文件顶部 import 区 + 新函数，放在 `browser_session` 之前）
- Test: `tests/test_proxy.py`（新建）

**Interfaces:**
- Produces: `claude_register.browser.parse_proxy(url: str | None) -> dict | None`
  - 空/空白/None → `None`
  - `"http://1.2.3.4:8080"` → `{"server": "http://1.2.3.4:8080"}`
  - `"socks5://u:p@h:1080"` → `{"server": "socks5://h:1080", "username": "u", "password": "p"}`
  - 非法（无 scheme / 无 host / 无 port / 端口非数字）→ `ValueError`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_proxy.py
import pytest

from claude_register.browser import parse_proxy


def test_empty_means_direct():
    assert parse_proxy("") is None
    assert parse_proxy("   ") is None
    assert parse_proxy(None) is None


def test_http_no_auth():
    assert parse_proxy("http://1.2.3.4:8080") == {"server": "http://1.2.3.4:8080"}


def test_socks5_with_auth():
    assert parse_proxy("socks5://user:pass@proxy.example.com:1080") == {
        "server": "socks5://proxy.example.com:1080",
        "username": "user",
        "password": "pass",
    }


def test_auth_percent_decoded():
    assert parse_proxy("http://u%40x:p%23w@h:8080") == {
        "server": "http://h:8080",
        "username": "u@x",
        "password": "p#w",
    }


@pytest.mark.parametrize("bad", [
    "1.2.3.4:8080",          # 无 scheme
    "http://:8080",          # 无 host
    "http://host",           # 无 port
    "http://host:abc",       # 端口非数字
    "://",                   # 乱码
])
def test_invalid_raises(bad):
    with pytest.raises(ValueError):
        parse_proxy(bad)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_proxy.py -v`
Expected: FAIL / ERROR，`ImportError: cannot import name 'parse_proxy'`

- [ ] **Step 3: 最小实现**

在 `claude_register/browser.py` 顶部 import 区加：

```python
from urllib.parse import unquote, urlsplit
```

在 `browser_session` 定义之前加：

```python
def parse_proxy(url: str | None) -> dict | None:
    """把代理 URL 解析成 Playwright 风格 proxy dict。

    空 → None（直连）。scheme/host/port 缺一 → ValueError（不静默降级直连，
    避免用户以为走了代理实际在裸奔）。
    """
    text = (url or "").strip()
    if not text:
        return None
    hint = (
        f"代理地址格式不对：{text!r}。应形如 "
        "http://host:port、http://user:pass@host:port 或 socks5://host:port"
    )
    try:
        parts = urlsplit(text)
        port = parts.port  # 端口非数字时这里抛 ValueError
    except ValueError as exc:
        raise ValueError(hint) from exc
    if not parts.scheme or not parts.hostname or port is None:
        raise ValueError(hint)
    proxy: dict = {"server": f"{parts.scheme}://{parts.hostname}:{port}"}
    if parts.username:
        proxy["username"] = unquote(parts.username)
    if parts.password:
        proxy["password"] = unquote(parts.password)
    return proxy
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_proxy.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 全量回归 + 提交**

Run: `uv run pytest`
Expected: 全部 PASS

```bash
git add claude_register/browser.py tests/test_proxy.py
git commit -m "feat(browser): parse_proxy 代理 URL 解析"
```

---

### Task 2: `browser_session` / flow 贯通代理参数

**Files:**
- Modify: `claude_register/browser.py:34-62`（`browser_session`）
- Modify: `claude_register/flow.py:53-61`（`run_browser` 签名与 `browser_session()` 调用）、`flow.py:146-184`（`run` 读配置并透传）
- Test: `tests/test_proxy.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `parse_proxy`
- Produces:
  - `browser_session(proxy: str | None = None)`——传入原始 URL 字符串，内部自行 `parse_proxy`
  - `run_browser(..., *, auto_login, code_timeout, proxy: str | None = None)`
  - `flow.run` 在 `config is not None` 分支读 `config.register_proxy`（Task 3 提供该字段；本 task 用 `getattr(config, "register_proxy", "")` 以便与 Task 3 顺序无关）

- [ ] **Step 1: 写失败测试（追加到 tests/test_proxy.py）**

```python
import inspect

from claude_register import browser, flow


def test_browser_session_accepts_proxy():
    sig = inspect.signature(browser.browser_session)
    assert "proxy" in sig.parameters


def test_run_browser_accepts_proxy():
    sig = inspect.signature(flow.run_browser)
    assert "proxy" in sig.parameters
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_proxy.py -v`
Expected: 新增两条 FAIL（`proxy` not in parameters）

- [ ] **Step 3: 实现**

`claude_register/browser.py` 中 `browser_session` 改为：

```python
@contextmanager
def browser_session(proxy: str | None = None):
    """启动 Camoufox（Firefox 系隐身浏览器）会话。

    headless="virtual" 自动包 Xvfb，适配无显示的容器，且比真 headless 更抗
    Cloudflare 检测；humanize 提供人性化光标移动；locale/geoip 让指纹统一
    （配了代理时 geoip 按代理出口 IP 匹配时区/地理指纹）。
    """
    proxy_cfg = parse_proxy(proxy)
    kwargs: dict = {}
    if proxy_cfg is not None:
        kwargs["proxy"] = proxy_cfg
        log(f"使用代理：{proxy_cfg['server']}")
    cm = Camoufox(
        headless="virtual",
        humanize=True,
        locale="en-US",
        geoip=True,
        window=(1280, 900),
        **kwargs,
    )
```

（`try: cm.__enter__() ...` 起的其余部分不动。）

`claude_register/flow.py`：

`run_browser` 签名加参数并透传（`flow.py:53-61`）：

```python
def run_browser(
    client: AnyMailClient,
    mailbox: Mailbox,
    since: str,
    *,
    auto_login: bool,
    code_timeout: float,
    proxy: str | None = None,
) -> None:
    with browser_session(proxy=proxy) as browser:
```

`run` 里（`config is not None` 分支内 `code_timeout = ...` 之后加一行；`else` 分支后保证有默认）：

```python
    proxy: str | None = None
    if config is not None:
        ...
        code_timeout = config.register_login_timeout
        proxy = getattr(config, "register_proxy", "") or None
```

`run_browser(...)` 调用处加 `proxy=proxy,`。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_proxy.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 全量回归 + 提交**

Run: `uv run pytest`
Expected: 全部 PASS

```bash
git add claude_register/browser.py claude_register/flow.py tests/test_proxy.py
git commit -m "feat(register): 浏览器会话支持代理，flow 从配置贯通"
```

---

### Task 3: Config 增加 `register_proxy` 字段

**Files:**
- Modify: `server/config_store.py`（`Config` dataclass、`load_config`、`_FIELD_MAP`）
- Test: `tests/test_config_store.py`（追加）

**Interfaces:**
- Consumes: 无
- Produces: `Config.register_proxy: str = ""`；config.yaml 持久化在 `register.proxy`；`/api/config` GET/PUT 自动带上该字段（app 层按 `_FIELD_MAP` 通用处理，无需改 app.py）。**不脱敏**（见 spec）。

- [ ] **Step 1: 写失败测试（追加到 tests/test_config_store.py，沿用该文件现有 fixture/风格）**

```python
def test_register_proxy_roundtrip(tmp_path):
    from server.config_store import load_config, save_config

    path = tmp_path / "config.yaml"
    cfg = save_config(path, {"register_proxy": "http://user:pass@1.2.3.4:8080"})
    assert cfg.register_proxy == "http://user:pass@1.2.3.4:8080"
    assert load_config(path).register_proxy == "http://user:pass@1.2.3.4:8080"


def test_register_proxy_default_empty(tmp_path):
    from server.config_store import load_config

    assert load_config(tmp_path / "missing.yaml").register_proxy == ""


def test_register_proxy_not_redacted(tmp_path):
    from server.config_store import load_config, save_config, to_redacted_dict

    path = tmp_path / "config.yaml"
    save_config(path, {"register_proxy": "http://user:pass@1.2.3.4:8080"})
    d = to_redacted_dict(load_config(path))
    assert d["register_proxy"] == "http://user:pass@1.2.3.4:8080"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_config_store.py -v`
Expected: 新增三条 FAIL（`Config` 无 `register_proxy` 属性 / save_config 忽略未知字段导致断言失败）

- [ ] **Step 3: 实现**

`server/config_store.py`：

`Config` dataclass 末尾加：

```python
    register_proxy: str = ""
```

`load_config` 的 `return Config(...)` 里加：

```python
        register_proxy=str(reg.get("proxy", "") or ""),
```

`_FIELD_MAP` 加：

```python
    "register_proxy": ("register", "proxy"),
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_config_store.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 全量回归 + 提交**

Run: `uv run pytest`
Expected: 全部 PASS（此时 Task 2 的 `getattr(config, "register_proxy", "")` 命中真实字段）

```bash
git add server/config_store.py tests/test_config_store.py
git commit -m "feat(config): register.proxy 配置字段"
```

---

### Task 4: 设置页新增「注册代理」输入框

**Files:**
- Modify: `web/src/pages/Settings.jsx:7-17`（`FIELD_DEFS`）

**Interfaces:**
- Consumes: Task 3 的 `/api/config` 中的 `register_proxy` 字段（GET 回显、PUT 保存均由现有通用逻辑处理）
- Produces: 无（纯 UI）

- [ ] **Step 1: 实现**

`FIELD_DEFS` 数组末尾（`register_code_regex` 之后）加一项，并支持可选 placeholder：

```jsx
  { key: "register_proxy", label: "注册代理（留空直连）", type: "text",
    placeholder: "http://user:pass@host:port 或 socks5://host:port" },
```

渲染处（`Settings.jsx:89`）placeholder 改为：

```jsx
                placeholder={f.secret ? SECRET_PLACEHOLDER : f.placeholder ?? ""}
```

- [ ] **Step 2: 验证**

前端无测试基建，且按用户规约本地不跑构建。人工检查两处 diff 即可：新字段非 `secret`，不会被 `SECRET_FIELDS` 的删除逻辑误伤（该列表未改动）。

Run: `uv run pytest`
Expected: 全部 PASS（确认后端未被顺手改坏）

- [ ] **Step 3: 提交**

```bash
git add web/src/pages/Settings.jsx
git commit -m "feat(web): 设置页支持注册代理"
```
