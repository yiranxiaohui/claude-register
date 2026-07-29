# claude-register Web 管理面板 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给现有 CLI 工具 claude-register 加一层 Web 管理面板：网页看日志、手动触发注册、用 config.yaml 替代 .env 环境变量。

**Architecture:** 复用现有 `claude_register/` CLI 核心，新增 `server/` FastAPI 后端（配置读写、SQLite、后台单任务执行、SSE 实时日志、密码登录）与 `web/` React 前端。后端把前端静态资源和 API 挂同一端口。注册流程仍跑现成的同步 `flow.run()`，只是包在后台线程里、日志经 contextvar sink 捕获落库。

**Tech Stack:** Python 3.13 / FastAPI / uvicorn / SQLite（stdlib sqlite3）/ PyYAML / httpx / Camoufox+Playwright（现有）；前端 React + Vite，bun 构建；Docker 多阶段；GitHub Actions 推 ghcr.io。

## Global Constraints

- 打包/包管理用 **bun**（前端），后端依赖用 **uv**；本地/线上都不实际执行打包构建。
- 线上机器只 `docker pull` + 启动，**不在服务器上 build**。镜像由 CI（自托管 runner LXC 1001）构建推 `ghcr.io/yiranxiaohui/claude-register`。
- Python `requires-python = ">=3.13"`。
- 面板默认端口 **8790**（与 grok-register 的 8788 错开）。
- 持久化统一在 `data/` 卷：`data/claude-register.db` + `data/runs/<run_id>/`。
- CLI 必须保持可独立运行：`uv run main.py` 不回归。Web 层只做两处侵入——`config.py` 数据源、`console.py` 加 sink 钩子。
- 密码字段返回前端一律脱敏为 `••••`；保存时传空表示不修改。
- 一次只允许一个注册任务在跑（全局锁 + DB 最多一条 `running`）。
- 本期不做：定时/自动运行、session 导出、并发多任务。

---

### Task 1: 配置存储 `config_store.py`

把 `.env` 换成 `config.yaml`：定义配置数据结构、yaml 读写、密码脱敏。这是所有后续任务的数据源。

**Files:**
- Create: `server/__init__.py`（空）
- Create: `server/config_store.py`
- Test: `tests/test_config_store.py`
- Modify: `pyproject.toml`（加依赖 `fastapi`、`uvicorn[standard]`、`pyyaml`、`sse-starlette`；dev 加 `httpx`（已在依赖）测试用 `fastapi` 的 TestClient 需要 `httpx`，已有）

**Interfaces:**
- Produces:
  - `@dataclass Config`，字段：`panel_password: str`、`panel_port: int`、`anymail_api_key: str`、`anymail_base_url: str`、`anymail_domain: str`、`anymail_expires_hours: float`、`register_login_timeout: float`、`register_auto_login: bool`、`register_code_regex: str`
  - `load_config(path: Path) -> Config`（文件不存在返回带默认值的 Config）
  - `save_config(path: Path, updates: dict) -> Config`（updates 里 `panel_password` 为空串则保留原密码；写回 yaml；返回新 Config）
  - `to_redacted_dict(cfg: Config) -> dict`（`panel_password` 若非空显示 `"••••"`，`anymail_api_key` 同样脱敏）
  - `REDACTED = "••••"`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_store.py
from pathlib import Path
from server.config_store import load_config, save_config, to_redacted_dict, REDACTED


def test_load_missing_returns_defaults(tmp_path):
    cfg = load_config(tmp_path / "nope.yaml")
    assert cfg.panel_port == 8790
    assert cfg.anymail_expires_hours == 24.0
    assert cfg.register_auto_login is True
    assert cfg.panel_password == ""


def test_save_then_load_roundtrip(tmp_path):
    p = tmp_path / "config.yaml"
    save_config(p, {"panel_password": "secret", "anymail_api_key": "ak_1",
                    "anymail_base_url": "https://mail.example.com"})
    cfg = load_config(p)
    assert cfg.panel_password == "secret"
    assert cfg.anymail_api_key == "ak_1"
    assert cfg.anymail_base_url == "https://mail.example.com"


def test_save_empty_password_keeps_existing(tmp_path):
    p = tmp_path / "config.yaml"
    save_config(p, {"panel_password": "secret"})
    save_config(p, {"panel_password": "", "anymail_domain": "example.com"})
    cfg = load_config(p)
    assert cfg.panel_password == "secret"
    assert cfg.anymail_domain == "example.com"


def test_redacted_hides_secrets(tmp_path):
    cfg = load_config(tmp_path / "nope.yaml")
    object.__setattr__(cfg, "panel_password", "secret")
    object.__setattr__(cfg, "anymail_api_key", "ak_1")
    d = to_redacted_dict(cfg)
    assert d["panel_password"] == REDACTED
    assert d["anymail_api_key"] == REDACTED
    assert d["panel_port"] == 8790
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config_store.py -v`
Expected: FAIL（ModuleNotFoundError: server.config_store）

- [ ] **Step 3: 加依赖并实现**

先在 `pyproject.toml` 的 `dependencies` 加 `"fastapi>=0.115"`、`"uvicorn[standard]>=0.34"`、`"pyyaml>=6.0"`、`"sse-starlette>=2.1"`，然后 `uv sync`。

```python
# server/config_store.py
"""config.yaml 读写 + 密码脱敏。替代 .env。"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import yaml

REDACTED = "••••"


@dataclass(frozen=True)
class Config:
    panel_password: str = ""
    panel_port: int = 8790
    anymail_api_key: str = ""
    anymail_base_url: str = ""
    anymail_domain: str = ""
    anymail_expires_hours: float = 24.0
    register_login_timeout: float = 120.0
    register_auto_login: bool = True
    register_code_regex: str = ""


def load_config(path: Path) -> Config:
    if not Path(path).is_file():
        return Config()
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    panel = raw.get("panel", {}) or {}
    anymail = raw.get("anymail", {}) or {}
    reg = raw.get("register", {}) or {}
    return Config(
        panel_password=str(panel.get("password", "") or ""),
        panel_port=int(panel.get("port", 8790)),
        anymail_api_key=str(anymail.get("api_key", "") or ""),
        anymail_base_url=str(anymail.get("base_url", "") or ""),
        anymail_domain=str(anymail.get("domain", "") or ""),
        anymail_expires_hours=float(anymail.get("expires_hours", 24.0)),
        register_login_timeout=float(reg.get("login_timeout", 120.0)),
        register_auto_login=bool(reg.get("auto_login", True)),
        register_code_regex=str(reg.get("code_regex", "") or ""),
    )


_FIELD_MAP = {
    "panel_password": ("panel", "password"),
    "panel_port": ("panel", "port"),
    "anymail_api_key": ("anymail", "api_key"),
    "anymail_base_url": ("anymail", "base_url"),
    "anymail_domain": ("anymail", "domain"),
    "anymail_expires_hours": ("anymail", "expires_hours"),
    "register_login_timeout": ("register", "login_timeout"),
    "register_auto_login": ("register", "auto_login"),
    "register_code_regex": ("register", "code_regex"),
}


def save_config(path: Path, updates: dict) -> Config:
    cfg = load_config(path)
    # 密码/密钥留空 = 不修改
    clean = dict(updates)
    for secret in ("panel_password", "anymail_api_key"):
        if secret in clean and clean[secret] in ("", REDACTED, None):
            clean.pop(secret)
    cfg = replace(cfg, **{k: v for k, v in clean.items() if k in _FIELD_MAP})
    out: dict = {"panel": {}, "anymail": {}, "register": {}}
    for field, (section, key) in _FIELD_MAP.items():
        out[section][key] = getattr(cfg, field)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(yaml.safe_dump(out, allow_unicode=True, sort_keys=False),
                          encoding="utf-8")
    return cfg


def to_redacted_dict(cfg: Config) -> dict:
    d = {f: getattr(cfg, f) for f in _FIELD_MAP}
    for secret in ("panel_password", "anymail_api_key"):
        if d[secret]:
            d[secret] = REDACTED
    return d
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config_store.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
git add server/__init__.py server/config_store.py tests/test_config_store.py pyproject.toml uv.lock
git commit -m "feat(server): config.yaml 读写 + 密码脱敏（config_store）"
```

---

### Task 2: 让 CLI 核心接受 Config（数据源 env → 对象）

现在 env 变量散在 `AnyMailClient.__init__`、`mailbox.py`、`anymail.py:397`、`flow.run`。改成让 `flow.run` 接受一个 `Config`，显式往下传，env 回落逻辑保留（CLI 不给 config 时仍能用 .env）。

**Files:**
- Modify: `claude_register/anymail.py`（`AnyMailClient.__init__` 已接受 `base_url/api_key/domain` 参数；`poll_code` 的 `code_regex` 已可传入——确认调用链传参）
- Modify: `claude_register/mailbox.py`（`prepare_mailbox` 接受 `expires_hours` 参数，默认仍回落 env）
- Modify: `claude_register/flow.py`（`run` 增加 `config: Config | None = None`，从中取 api_key/base_url/domain/expires_hours/login_timeout/auto_login/code_regex）
- Test: `tests/test_flow_config.py`

**Interfaces:**
- Consumes: `server.config_store.Config`（Task 1）
- Produces:
  - `claude_register.mailbox.prepare_mailbox(client, *, email=None, domain=None, expires_hours: float | None = None) -> tuple[Mailbox, str]`
  - `claude_register.flow.run(*, email=None, domain=None, auto_login=True, code_timeout=120.0, config=None) -> None`（config 提供时覆盖 client 构造与各超时/正则；不提供时维持旧 env 行为）
  - `AnyMailClient(..., code_regex: str | None = None)` 保留：`poll_code` 已有 `code_regex` 参数，flow 从 config 传入

- [ ] **Step 1: Write the failing test**

```python
# tests/test_flow_config.py
import inspect
from claude_register import flow, mailbox


def test_flow_run_accepts_config():
    sig = inspect.signature(flow.run)
    assert "config" in sig.parameters


def test_prepare_mailbox_accepts_expires_hours():
    sig = inspect.signature(mailbox.prepare_mailbox)
    assert "expires_hours" in sig.parameters
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_flow_config.py -v`
Expected: FAIL（config / expires_hours 不在签名里）

- [ ] **Step 3: 实现**

`claude_register/mailbox.py` — 找到 `expires_hours = resolve_expires_hours(os.getenv("ANYMAIL_EXPIRES_HOURS"))`，改为参数优先：

```python
def prepare_mailbox(client, *, email=None, domain=None, expires_hours=None):
    if expires_hours is None:
        expires_hours = resolve_expires_hours(os.getenv("ANYMAIL_EXPIRES_HOURS"))
    # ...原有逻辑不变，使用局部 expires_hours...
```

`claude_register/flow.py` — `run` 增加 config 分支：

```python
def run(*, email=None, domain=None, auto_login=True, code_timeout=120.0, config=None):
    load_dotenv()
    if config is not None:
        client = AnyMailClient(
            base_url=config.anymail_base_url or None,
            api_key=config.anymail_api_key or None,
            domain=config.anymail_domain or domain,
            code_regex=config.register_code_regex or None,
        )
        expires_hours = None if config.anymail_expires_hours <= 0 else config.anymail_expires_hours
        auto_login = config.register_auto_login
        code_timeout = config.register_login_timeout
    else:
        client = AnyMailClient(domain=domain)
        expires_hours = None
    if email and domain:
        log("已指定 --email，忽略 --domain（邮箱已含后缀）。")
    mailbox, since = prepare_mailbox(client, email=email, domain=domain, expires_hours=expires_hours)
    log(f"本次邮箱：{mailbox.email} (id={mailbox.id or 'new'})")
    run_browser(client, mailbox, since, auto_login=auto_login, code_timeout=code_timeout)
    log("完成。")
    banner(f"邮箱：{mailbox.email}")
    if mailbox.id:
        log(f"邮箱 id：{mailbox.id}")
    log("提示：邮箱默认 24 小时后被 AnyMail 清理，若要长期收信请调整有效期。")
```

确认 `AnyMailClient.__init__` 接受 `code_regex` 参数并存 `self.code_regex`，`poll_code` 默认用 `self.code_regex`（若当前是从 env 读，改为 `code_regex or self.code_regex or resolve_code_regex(os.getenv(...))`）。

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_flow_config.py tests/ -v`
Expected: PASS（新测试通过，且现有测试无回归）

- [ ] **Step 5: Commit**

```bash
git add claude_register/flow.py claude_register/mailbox.py claude_register/anymail.py tests/test_flow_config.py
git commit -m "refactor(core): flow.run 接受 Config，数据源从 env 改为显式注入（保留 env 回落）"
```

---

### Task 3: `console.py` 加 contextvar 日志 sink

让 `log()`/`banner()` 在 Web 模式把日志推给当前 run 的 sink，CLI 模式照常 print。不触碰任何调用点。

**Files:**
- Modify: `claude_register/console.py`
- Test: `tests/test_console_sink.py`

**Interfaces:**
- Produces:
  - `claude_register.console.set_sink(fn: Callable[[str], None] | None) -> contextvars.Token`
  - `claude_register.console.reset_sink(token) -> None`
  - `log(msg)` / `banner(msg)`：当前 contextvar 有 sink → 调 sink(msg)（banner 传格式化后的文本）；否则 print

- [ ] **Step 1: Write the failing test**

```python
# tests/test_console_sink.py
from claude_register import console


def test_log_prints_when_no_sink(capsys):
    console.log("hello")
    assert "hello" in capsys.readouterr().out


def test_log_routes_to_sink():
    lines = []
    token = console.set_sink(lines.append)
    try:
        console.log("to-sink")
        console.banner("BAN")
    finally:
        console.reset_sink(token)
    assert "to-sink" in lines
    assert any("BAN" in x for x in lines)


def test_sink_reset_restores_print(capsys):
    token = console.set_sink(lambda _: None)
    console.reset_sink(token)
    console.log("back-to-stdout")
    assert "back-to-stdout" in capsys.readouterr().out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_console_sink.py -v`
Expected: FAIL（set_sink 不存在）

- [ ] **Step 3: 实现**

```python
# claude_register/console.py
"""终端 I/O：日志、输入、醒目横幅。Web 模式经 contextvar sink 捕获。"""
from __future__ import annotations

import contextvars
from collections.abc import Callable

_sink: contextvars.ContextVar[Callable[[str], None] | None] = contextvars.ContextVar(
    "console_sink", default=None
)


def set_sink(fn: Callable[[str], None] | None) -> contextvars.Token:
    return _sink.set(fn)


def reset_sink(token: contextvars.Token) -> None:
    _sink.reset(token)


def log(msg: str) -> None:
    sink = _sink.get()
    if sink is not None:
        sink(msg)
    else:
        print(msg, flush=True)


def prompt(msg: str) -> str:
    try:
        return input(msg).strip()
    except EOFError:
        return ""


def banner(msg: str) -> None:
    line = "=" * max(40, len(msg) + 4)
    text = f"\n{line}\n  {msg}\n{line}\n"
    sink = _sink.get()
    if sink is not None:
        sink(text)
    else:
        print(text, flush=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_console_sink.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add claude_register/console.py tests/test_console_sink.py
git commit -m "feat(core): console 加 contextvar 日志 sink（Web 捕获，CLI 照常 print）"
```

---

### Task 4: SQLite 数据层 `db.py`

runs 与 accounts 两张表的建表 + 纯数据访问。

**Files:**
- Create: `server/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Produces:
  - `init_db(path: Path) -> sqlite3.Connection`（建表 + 返回连接，`check_same_thread=False`，`row_factory=sqlite3.Row`）
  - `create_run(conn, email, domain, output_dir) -> int`（status='running'，started_at=now iso，返回 run id）
  - `finish_run(conn, run_id, status) -> None`（写 finished_at + status）
  - `list_runs(conn, limit=50, offset=0) -> list[dict]`
  - `get_run(conn, run_id) -> dict | None`
  - `active_run(conn) -> dict | None`（status='running' 的那条，没有返回 None）
  - `mark_stale_running_as_failed(conn) -> int`（启动清理，返回改动条数）
  - `upsert_account(conn, email, domain, expires_at, mailbox_id, last_run_id, status) -> None`（按 email 唯一）
  - `list_accounts(conn) -> list[dict]`
  - 时间戳用调用方传入的 `now: str`（避免测试依赖真实时间）：`create_run(conn, email, domain, output_dir, now)`、`finish_run(conn, run_id, status, now)`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_db.py
from server import db


def test_create_and_get_run(tmp_path):
    conn = db.init_db(tmp_path / "t.db")
    rid = db.create_run(conn, "a@x.com", "x.com", "data/runs/1", "2026-07-29T00:00:00Z")
    row = db.get_run(conn, rid)
    assert row["email"] == "a@x.com"
    assert row["status"] == "running"


def test_active_run_and_finish(tmp_path):
    conn = db.init_db(tmp_path / "t.db")
    rid = db.create_run(conn, "a@x.com", "x.com", "d", "2026-07-29T00:00:00Z")
    assert db.active_run(conn)["id"] == rid
    db.finish_run(conn, rid, "success", "2026-07-29T00:01:00Z")
    assert db.active_run(conn) is None
    assert db.get_run(conn, rid)["status"] == "success"


def test_mark_stale(tmp_path):
    conn = db.init_db(tmp_path / "t.db")
    db.create_run(conn, "a@x.com", "x.com", "d", "2026-07-29T00:00:00Z")
    assert db.mark_stale_running_as_failed(conn) == 1
    assert db.active_run(conn) is None


def test_upsert_account_unique(tmp_path):
    conn = db.init_db(tmp_path / "t.db")
    db.upsert_account(conn, "a@x.com", "x.com", None, "m1", 1, "success")
    db.upsert_account(conn, "a@x.com", "x.com", None, "m2", 2, "success")
    rows = db.list_accounts(conn)
    assert len(rows) == 1
    assert rows[0]["mailbox_id"] == "m2"
    assert rows[0]["last_run_id"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_db.py -v`
Expected: FAIL（server.db 不存在）

- [ ] **Step 3: 实现**

```python
# server/db.py
"""SQLite 数据访问：runs / accounts。纯数据层，无业务逻辑。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT, domain TEXT, status TEXT,
  started_at TEXT, finished_at TEXT, output_dir TEXT
);
CREATE TABLE IF NOT EXISTS accounts (
  email TEXT UNIQUE, domain TEXT, created_at TEXT, expires_at TEXT,
  mailbox_id TEXT, last_run_id INTEGER, status TEXT
);
"""


def init_db(path: Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def create_run(conn, email, domain, output_dir, now) -> int:
    cur = conn.execute(
        "INSERT INTO runs(email,domain,status,started_at,output_dir) "
        "VALUES(?,?,'running',?,?)",
        (email, domain, now, output_dir),
    )
    conn.commit()
    return cur.lastrowid


def finish_run(conn, run_id, status, now) -> None:
    conn.execute("UPDATE runs SET status=?, finished_at=? WHERE id=?",
                 (status, now, run_id))
    conn.commit()


def _row(r) -> dict | None:
    return dict(r) if r is not None else None


def list_runs(conn, limit=50, offset=0) -> list[dict]:
    rows = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ? OFFSET ?",
                        (limit, offset)).fetchall()
    return [dict(r) for r in rows]


def get_run(conn, run_id) -> dict | None:
    return _row(conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())


def active_run(conn) -> dict | None:
    return _row(conn.execute(
        "SELECT * FROM runs WHERE status='running' ORDER BY id DESC LIMIT 1").fetchone())


def mark_stale_running_as_failed(conn) -> int:
    cur = conn.execute("UPDATE runs SET status='failed' WHERE status='running'")
    conn.commit()
    return cur.rowcount


def upsert_account(conn, email, domain, expires_at, mailbox_id, last_run_id, status) -> None:
    conn.execute(
        "INSERT INTO accounts(email,domain,created_at,expires_at,mailbox_id,last_run_id,status) "
        "VALUES(?,?,?,?,?,?,?) "
        "ON CONFLICT(email) DO UPDATE SET domain=excluded.domain, "
        "expires_at=excluded.expires_at, mailbox_id=excluded.mailbox_id, "
        "last_run_id=excluded.last_run_id, status=excluded.status",
        (email, domain, "", expires_at, mailbox_id, last_run_id, status),
    )
    conn.commit()


def list_accounts(conn) -> list[dict]:
    rows = conn.execute("SELECT * FROM accounts ORDER BY rowid DESC").fetchall()
    return [dict(r) for r in rows]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_db.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
git add server/db.py tests/test_db.py
git commit -m "feat(server): SQLite 数据层 runs/accounts（db）"
```

---

### Task 5: 后台单任务执行 `runner.py`

一把全局锁 + 后台线程跑 `flow.run`，sink 把日志写 `log.txt` 并 push 进内存队列供 SSE 消费，状态落库。

**Files:**
- Create: `server/runner.py`
- Modify: `claude_register/browser.py`（`screenshot` 支持可配置输出目录，让截图落进当前 run 目录而非固定 `output/`）
- Test: `tests/test_runner.py`

**Interfaces:**
- Consumes: `server.db`（Task 4）、`server.config_store.Config`（Task 1）、`claude_register.console.set_sink/reset_sink`（Task 3）、`claude_register.flow.run`（Task 2）
- Produces:
  - `class Runner`，构造 `Runner(conn, data_dir: Path, now_fn: Callable[[], str])`
  - `Runner.start(config: Config, *, email=None, domain=None, flow_fn=flow.run) -> int`（已有任务在跑抛 `RunnerBusy`；否则建 run、起线程、返回 run_id）；`flow_fn` 可注入便于测试
  - `Runner.subscribe(run_id) -> queue.Queue`（返回该 run 的日志队列，非当前活动 run 返回 None）
  - `Runner.is_busy() -> bool`
  - `exception RunnerBusy(Exception)`
  - 队列消息约定：普通日志行 `{"type":"log","line":str}`；截图 `{"type":"screenshot","name":str}`（本期简化：截图不单独推，仅日志中体现文件名，前端从 run 详情读取截图目录）；结束 `{"type":"done","status":str}`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_runner.py
import time
import pytest
from server import db, runner
from server.config_store import Config


def _now():
    return "2026-07-29T00:00:00Z"


def test_start_runs_flow_and_records_success(tmp_path):
    conn = db.init_db(tmp_path / "t.db")
    r = runner.Runner(conn, tmp_path, _now)
    calls = {}

    def fake_flow(*, email=None, domain=None, config=None, **kw):
        from claude_register import console
        console.log("hi from flow")
        calls["email"] = email

    rid = r.start(Config(), email="a@x.com", flow_fn=fake_flow)
    # 等线程结束
    for _ in range(50):
        if db.get_run(conn, rid)["status"] != "running":
            break
        time.sleep(0.05)
    row = db.get_run(conn, rid)
    assert row["status"] == "success"
    log_txt = (tmp_path / "runs" / str(rid) / "log.txt").read_text()
    assert "hi from flow" in log_txt


def test_start_twice_is_busy(tmp_path):
    conn = db.init_db(tmp_path / "t.db")
    r = runner.Runner(conn, tmp_path, _now)

    def slow_flow(**kw):
        time.sleep(0.5)

    r.start(Config(), email="a@x.com", flow_fn=slow_flow)
    with pytest.raises(runner.RunnerBusy):
        r.start(Config(), email="b@x.com", flow_fn=slow_flow)


def test_flow_exception_marks_failed(tmp_path):
    conn = db.init_db(tmp_path / "t.db")
    r = runner.Runner(conn, tmp_path, _now)

    def boom(**kw):
        raise RuntimeError("kaboom")

    rid = r.start(Config(), email="a@x.com", flow_fn=boom)
    for _ in range(50):
        if db.get_run(conn, rid)["status"] != "running":
            break
        time.sleep(0.05)
    assert db.get_run(conn, rid)["status"] == "failed"
    assert "kaboom" in (tmp_path / "runs" / str(rid) / "log.txt").read_text()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_runner.py -v`
Expected: FAIL（server.runner 不存在）

- [ ] **Step 3: 实现**

```python
# server/runner.py
"""后台单任务执行：全局锁 + 线程 + sink 捕获日志落库。"""
from __future__ import annotations

import queue
import threading
from pathlib import Path

from claude_register import console
from claude_register import flow as flow_module
from server import db


class RunnerBusy(Exception):
    pass


class Runner:
    def __init__(self, conn, data_dir: Path, now_fn):
        self.conn = conn
        self.data_dir = Path(data_dir)
        self.now_fn = now_fn
        self._lock = threading.Lock()
        self._active_id: int | None = None
        self._queues: dict[int, queue.Queue] = {}

    def is_busy(self) -> bool:
        with self._lock:
            return self._active_id is not None

    def subscribe(self, run_id):
        return self._queues.get(run_id)

    def start(self, config, *, email=None, domain=None, flow_fn=None):
        flow_fn = flow_fn or flow_module.run
        with self._lock:
            if self._active_id is not None:
                raise RunnerBusy("已有注册任务在运行")
            run_dir = self.data_dir / "runs"
            rid = db.create_run(
                self.conn, email or "", domain or config.anymail_domain,
                "", self.now_fn(),
            )
            out_dir = run_dir / str(rid)
            out_dir.mkdir(parents=True, exist_ok=True)
            db.finish  # noop ref to keep import; actual finish in thread
            self.conn.execute("UPDATE runs SET output_dir=? WHERE id=?",
                              (str(out_dir), rid))
            self.conn.commit()
            q: queue.Queue = queue.Queue()
            self._queues[rid] = q
            self._active_id = rid
        t = threading.Thread(target=self._run, args=(rid, out_dir, config, email, domain, flow_fn, q),
                             daemon=True)
        t.start()
        return rid

    def _run(self, rid, out_dir: Path, config, email, domain, flow_fn, q):
        log_path = out_dir / "log.txt"
        fh = log_path.open("a", encoding="utf-8")

        def sink(msg: str):
            fh.write(msg + "\n")
            fh.flush()
            q.put({"type": "log", "line": msg})

        token = console.set_sink(sink)
        status = "success"
        try:
            flow_fn(email=email, domain=domain, config=config)
        except Exception as exc:  # noqa: BLE001
            status = "failed"
            sink(f"运行出错：{exc!r}")
        finally:
            console.reset_sink(token)
            db.finish_run(self.conn, rid, status, self.now_fn())
            q.put({"type": "done", "status": status})
            fh.close()
            with self._lock:
                self._active_id = None
```

注意：删掉上面 `db.finish` 那行占位（写实现时不要保留）；`output_dir` 在建 run 后用 UPDATE 补写。

**截图落进 run 目录：** `claude_register/browser.py` 的 `OUTPUT_DIR` 是模块级固定 `output/`。加一个 contextvar/setter 让它可被 runner 指向当前 `out_dir`：

```python
# browser.py 顶部
import contextvars
_output_dir: contextvars.ContextVar = contextvars.ContextVar("output_dir", default=OUTPUT_DIR)

def set_output_dir(path):
    return _output_dir.set(Path(path))

def screenshot(page, name):
    d = _output_dir.get()
    d.mkdir(parents=True, exist_ok=True)
    path = d / name
    page.screenshot(path=path, full_page=True)
    log(f"截图已保存：{path}")
    return path
```

runner 的 `_run` 里在 `set_sink` 之后加 `token2 = browser.set_output_dir(out_dir)`，finally 里 `browser._output_dir.reset(token2)`。这样 Task 7 的 `run_detail` glob `out_dir/*.png` 才能读到截图。

**needs_manual 状态：** 现有 `flow.run` 无返回值区分「Cloudflare 超时/需人工 hCaptcha」这类半成品状态，一律走异常→failed 或正常→success。本任务先只落 success/failed 两态；needs_manual 的精细区分需要 flow 回传信号（如返回一个状态枚举），列为紧接的后续增强，不在本任务实现——但 DB 的 status 字段已预留该取值，前端按黄色渲染的分支也预留，等 flow 支持后接上即可。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_runner.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add server/runner.py tests/test_runner.py
git commit -m "feat(server): 后台单任务执行 + 日志 sink 落库（runner）"
```

---

### Task 6: 密码登录 `auth.py`

密码校验 + 签名 cookie。单用户。

**Files:**
- Create: `server/auth.py`
- Test: `tests/test_auth.py`

**Interfaces:**
- Produces:
  - `make_token(password: str, secret: str) -> str`（HMAC 签名，内容固定 `"ok"`）
  - `verify_token(token: str, password: str, secret: str) -> bool`
  - `COOKIE_NAME = "cr_session"`
  - secret 由 `password + 固定 salt` 派生，密码改了旧 cookie 自动失效

- [ ] **Step 1: Write the failing test**

```python
# tests/test_auth.py
from server import auth


def test_token_roundtrip():
    t = auth.make_token("pw", "secret")
    assert auth.verify_token(t, "pw", "secret") is True


def test_wrong_password_rejected():
    t = auth.make_token("pw", "secret")
    assert auth.verify_token(t, "different", "secret") is False


def test_tampered_token_rejected():
    assert auth.verify_token("garbage", "pw", "secret") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_auth.py -v`
Expected: FAIL（server.auth 不存在）

- [ ] **Step 3: 实现**

```python
# server/auth.py
"""密码登录：HMAC 签名 cookie。单用户。"""
from __future__ import annotations

import hashlib
import hmac

COOKIE_NAME = "cr_session"
_PAYLOAD = b"ok"


def _key(password: str, secret: str) -> bytes:
    return hashlib.sha256(f"{secret}:{password}".encode()).digest()


def make_token(password: str, secret: str) -> str:
    sig = hmac.new(_key(password, secret), _PAYLOAD, hashlib.sha256).hexdigest()
    return sig


def verify_token(token: str, password: str, secret: str) -> bool:
    if not token or not password:
        return False
    expected = make_token(password, secret)
    return hmac.compare_digest(token, expected)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_auth.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add server/auth.py tests/test_auth.py
git commit -m "feat(server): 密码登录 HMAC cookie（auth）"
```

---

### Task 7: FastAPI 应用 `app.py`（路由 + SSE + 静态托管）

把前面各单元拼成 HTTP 服务。

**Files:**
- Create: `server/app.py`
- Create: `server/deps.py`（应用级单例：Config 路径、DB 连接、Runner）
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: config_store、db、runner、auth（Task 1/4/5/6）
- Produces（FastAPI app 对象 `app`，供 uvicorn `server.app:app`）：
  - `POST /api/login` body `{password}` → set-cookie，返回 `{ok:true}`；错密码 401
  - `GET /api/config` → 脱敏配置（需登录）
  - `PUT /api/config` body 部分字段 → 保存 + 返回脱敏配置（需登录）
  - `POST /api/runs` body `{email?, domain?}` → `{run_id}`；忙时 409（需登录）
  - `GET /api/runs?limit&offset` → 列表（需登录）
  - `GET /api/runs/{id}` → 详情 + `log`（读 log.txt）+ `screenshots`（列 out_dir 下 *.png）（需登录）
  - `GET /api/runs/{id}/stream` → SSE：先补发 log.txt 已有内容，再从队列续流，收到 done 关闭（需登录）
  - `GET /api/accounts` → 列表（需登录）
  - `POST /api/accounts/{email}/rerun` → `{run_id}`（需登录）
  - 静态：`/runs/<id>/<file>` 映射 data/runs；`/` 及静态资源映射 `web/dist`
  - 依赖注入 `require_auth`（校验 cookie，失败 401）

- [ ] **Step 1: Write the failing test**

```python
# tests/test_app.py
from fastapi.testclient import TestClient
from server.app import create_app


def _client(tmp_path):
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml",
                     now_fn=lambda: "2026-07-29T00:00:00Z")
    return TestClient(app)


def _login(c):
    # 首次无密码：允许空密码登录设初始密码前，先写配置
    c.put("/api/config", cookies={}, json={})  # 无密码时 require_auth 放行? 见实现


def test_login_wrong_password(tmp_path):
    # 预置密码
    from server.config_store import save_config
    save_config(tmp_path / "config.yaml", {"panel_password": "pw"})
    c = _client(tmp_path)
    r = c.post("/api/login", json={"password": "nope"})
    assert r.status_code == 401


def test_login_and_get_config_redacted(tmp_path):
    from server.config_store import save_config
    save_config(tmp_path / "config.yaml", {"panel_password": "pw", "anymail_api_key": "ak_1"})
    c = _client(tmp_path)
    assert c.post("/api/login", json={"password": "pw"}).status_code == 200
    r = c.get("/api/config")
    assert r.status_code == 200
    assert r.json()["anymail_api_key"] == "••••"


def test_runs_requires_auth(tmp_path):
    from server.config_store import save_config
    save_config(tmp_path / "config.yaml", {"panel_password": "pw"})
    c = _client(tmp_path)
    assert c.get("/api/runs").status_code == 401


def test_start_run_busy_returns_409(tmp_path, monkeypatch):
    import time
    from server.config_store import save_config
    save_config(tmp_path / "config.yaml", {"panel_password": "pw",
                "anymail_api_key": "ak", "anymail_base_url": "https://m"})
    c = _client(tmp_path)
    c.post("/api/login", json={"password": "pw"})
    # 注入慢 flow：通过 app.state.runner 替换 flow_fn 默认值不便，改用 monkeypatch flow.run
    import claude_register.flow as flow
    monkeypatch.setattr(flow, "run", lambda **kw: time.sleep(0.5))
    r1 = c.post("/api/runs", json={"email": "a@x.com"})
    assert r1.status_code == 200
    r2 = c.post("/api/runs", json={"email": "b@x.com"})
    assert r2.status_code == 409
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_app.py -v`
Expected: FAIL（server.app 不存在）

- [ ] **Step 3: 实现**

```python
# server/deps.py
"""应用级单例装配。"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from server import db
from server.config_store import Config, load_config
from server.runner import Runner


def default_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AppState:
    def __init__(self, data_dir: Path, config_path: Path, now_fn=default_now):
        self.data_dir = Path(data_dir)
        self.config_path = Path(config_path)
        self.now_fn = now_fn
        self.conn = db.init_db(self.data_dir / "claude-register.db")
        db.mark_stale_running_as_failed(self.conn)  # 重启清理残留 running
        self.runner = Runner(self.conn, self.data_dir, now_fn)
        self.secret = "claude-register-panel"

    def config(self) -> Config:
        return load_config(self.config_path)
```

```python
# server/app.py
"""FastAPI：路由 + SSE + 静态托管。薄，活派给 config_store/db/runner/auth。"""
from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from claude_register import flow
from server import auth, db
from server.config_store import save_config, to_redacted_dict
from server.deps import AppState
from server.runner import RunnerBusy

WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"


def create_app(*, data_dir, config_path, now_fn=None) -> FastAPI:
    from server.deps import default_now
    state = AppState(Path(data_dir), Path(config_path), now_fn or default_now)
    app = FastAPI()
    app.state.cr = state

    def require_auth(request: Request):
        cfg = state.config()
        if not cfg.panel_password:
            return  # 未设密码：放行（首次配置用）
        token = request.cookies.get(auth.COOKIE_NAME, "")
        if not auth.verify_token(token, cfg.panel_password, state.secret):
            raise HTTPException(status_code=401, detail="未登录")

    @app.post("/api/login")
    async def login(request: Request, response: Response):
        body = await request.json()
        cfg = state.config()
        if not auth.verify_token(auth.make_token(body.get("password", ""), state.secret),
                                 cfg.panel_password, state.secret):
            raise HTTPException(status_code=401, detail="密码错误")
        token = auth.make_token(cfg.panel_password, state.secret)
        response.set_cookie(auth.COOKIE_NAME, token, httponly=True, samesite="lax")
        return {"ok": True}

    @app.get("/api/config")
    def get_config(_=Depends(require_auth)):
        return to_redacted_dict(state.config())

    @app.put("/api/config")
    async def put_config(request: Request, _=Depends(require_auth)):
        body = await request.json()
        cfg = save_config(state.config_path, body)
        return to_redacted_dict(cfg)

    @app.post("/api/runs")
    def start_run(body: dict, _=Depends(require_auth)):
        try:
            rid = state.runner.start(state.config(),
                                     email=body.get("email"), domain=body.get("domain"),
                                     flow_fn=flow.run)
        except RunnerBusy:
            raise HTTPException(status_code=409, detail="已有任务在运行")
        return {"run_id": rid}

    @app.get("/api/runs")
    def get_runs(limit: int = 50, offset: int = 0, _=Depends(require_auth)):
        return db.list_runs(state.conn, limit, offset)

    @app.get("/api/runs/{run_id}")
    def run_detail(run_id: int, _=Depends(require_auth)):
        row = db.get_run(state.conn, run_id)
        if not row:
            raise HTTPException(404)
        out = Path(row["output_dir"]) if row["output_dir"] else None
        log_txt = ""
        shots = []
        if out and out.exists():
            lp = out / "log.txt"
            if lp.exists():
                log_txt = lp.read_text(encoding="utf-8")
            shots = sorted(p.name for p in out.glob("*.png"))
        return {**row, "log": log_txt, "screenshots": shots}

    @app.get("/api/runs/{run_id}/stream")
    async def stream(run_id: int, request: Request, _=Depends(require_auth)):
        row = db.get_run(state.conn, run_id)
        if not row:
            raise HTTPException(404)

        async def gen():
            out = Path(row["output_dir"]) if row["output_dir"] else None
            lp = out / "log.txt" if out else None
            if lp and lp.exists():
                for line in lp.read_text(encoding="utf-8").splitlines():
                    yield {"event": "log", "data": line}
            q = state.runner.subscribe(run_id)
            if q is None:
                yield {"event": "done", "data": row["status"]}
                return
            import asyncio
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = q.get(timeout=0.5)
                except Exception:
                    await asyncio.sleep(0)
                    continue
                if msg["type"] == "log":
                    yield {"event": "log", "data": msg["line"]}
                elif msg["type"] == "done":
                    yield {"event": "done", "data": msg["status"]}
                    break

        return EventSourceResponse(gen())

    @app.get("/api/accounts")
    def accounts(_=Depends(require_auth)):
        return db.list_accounts(state.conn)

    @app.post("/api/accounts/{email}/rerun")
    def rerun(email: str, _=Depends(require_auth)):
        try:
            rid = state.runner.start(state.config(), email=email, flow_fn=flow.run)
        except RunnerBusy:
            raise HTTPException(status_code=409, detail="已有任务在运行")
        return {"run_id": rid}

    # 截图静态
    runs_dir = state.data_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/runs", StaticFiles(directory=runs_dir), name="runs")

    # 前端（dist 存在才挂，测试环境无 dist 不报错）
    if WEB_DIST.exists():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")

    return app
```

注意 `POST /api/runs` 用 `body: dict` 直接收 JSON；上面测试里 `start_run` 成功后应把 account upsert——本任务先只起 run，account 落库放在 runner 成功回调里做更完整（增量：可在 runner `_run` success 分支里调 `db.upsert_account`，但邮箱/mailbox_id 需从 flow 回传，属后续增强，本期 accounts 至少能列出重跑用的历史 email，可由 runs 去重生成）。**为满足「账号列表」，本任务在 `GET /api/accounts` 改为：若 accounts 表为空则从 runs 表 distinct email 生成只读列表。** 更新实现：

```python
    @app.get("/api/accounts")
    def accounts(_=Depends(require_auth)):
        rows = db.list_accounts(state.conn)
        if rows:
            return rows
        seen = {}
        for r in db.list_runs(state.conn, 500, 0):
            e = r["email"]
            if e and e not in seen and r["status"] == "success":
                seen[e] = {"email": e, "domain": r["domain"],
                           "last_run_id": r["id"], "status": r["status"]}
        return list(seen.values())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_app.py -v`
Expected: PASS（4 passed；忙时 409 验证锁生效）

- [ ] **Step 5: Commit**

```bash
git add server/app.py server/deps.py tests/test_app.py
git commit -m "feat(server): FastAPI 路由 + SSE 实时日志 + 静态托管（app）"
```

---

### Task 8: 入口脚本 `serve.py` + CLI 改用 config.yaml

新增 `serve.py` 启服务；`main.py`/CLI 也能读 config.yaml（保持 env 兼容）。

**Files:**
- Create: `serve.py`（`uvicorn.run("server.app:app", ...)`，端口读 config）
- Modify: `main.py` / `claude_register/__main__.py`（新增 `--config` 选项，给到 `flow.run(config=...)`）
- Create: `config.example.yaml`
- Test: `tests/test_serve_smoke.py`（import serve 不报错 + create_app 可实例化）

**Interfaces:**
- Consumes: `server.app.create_app`、`server.config_store.load_config`
- Produces: `serve.main()`（读 config.yaml 的 port，起 uvicorn）

- [ ] **Step 1: Write the failing test**

```python
# tests/test_serve_smoke.py
def test_create_app_importable(tmp_path):
    from server.app import create_app
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "c.yaml")
    assert app is not None


def test_serve_module_has_main():
    import serve
    assert hasattr(serve, "main")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_serve_smoke.py -v`
Expected: FAIL（serve 不存在）

- [ ] **Step 3: 实现**

```python
# serve.py
"""启动 Web 管理面板。"""
from __future__ import annotations

from pathlib import Path

import uvicorn

from server.app import create_app
from server.config_store import load_config

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
CONFIG_PATH = ROOT / "config.yaml"

app = create_app(data_dir=DATA_DIR, config_path=CONFIG_PATH)


def main() -> None:
    cfg = load_config(CONFIG_PATH)
    uvicorn.run(app, host="0.0.0.0", port=cfg.panel_port)


if __name__ == "__main__":
    main()
```

`config.example.yaml`：

```yaml
panel:
  password: "改我"
  port: 8790
anymail:
  api_key: ""
  base_url: ""
  domain: ""
  expires_hours: 24
register:
  login_timeout: 120
  auto_login: true
  code_regex: ""
```

`main.py` 加 `--config PATH` 选项：给了就 `flow.run(config=load_config(path), email=..., domain=...)`；没给维持现状。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_serve_smoke.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: Commit**

```bash
git add serve.py config.example.yaml main.py claude_register/__main__.py tests/test_serve_smoke.py
git commit -m "feat: serve.py 启动面板 + CLI 支持 --config"
```

---

### Task 9: React 前端 `web/`

三页面：登录、主面板（触发注册 + 实时日志 + 运行历史 + 账号列表）、设置。bun 构建到 `web/dist`。

**Files:**
- Create: `web/package.json`、`web/vite.config.js`、`web/index.html`
- Create: `web/src/main.jsx`、`web/src/App.jsx`、`web/src/api.js`
- Create: `web/src/pages/Login.jsx`、`web/src/pages/Dashboard.jsx`、`web/src/pages/Settings.jsx`
- Test: 前端本期不写自动化测试；用 `bun run build` 产出 dist 作为验收

**Interfaces:**
- Consumes: Task 7 的 REST + SSE 接口
- Produces: `web/dist/` 静态资源（被 `server/app.py` 挂 `/`）

- [ ] **Step 1: 初始化前端工程**

```bash
mkdir -p web/src/pages
cd web
```

`web/package.json`：

```json
{
  "name": "claude-register-web",
  "private": true,
  "type": "module",
  "scripts": { "dev": "vite", "build": "vite build" },
  "dependencies": { "react": "^18.3.1", "react-dom": "^18.3.1" },
  "devDependencies": { "@vitejs/plugin-react": "^4.3.1", "vite": "^5.4.0" }
}
```

`web/vite.config.js`（dev 时把 /api、/runs 代理到 8790）：

```js
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist" },
  server: { proxy: { "/api": "http://localhost:8790", "/runs": "http://localhost:8790" } },
});
```

`web/index.html`：

```html
<!doctype html><html><head><meta charset="utf-8"><title>claude-register</title></head>
<body><div id="root"></div><script type="module" src="/src/main.jsx"></script></body></html>
```

- [ ] **Step 2: API 封装 + 页面**

`web/src/api.js`：

```js
const j = (r) => { if (!r.ok) throw new Error(r.status); return r.json(); };
export const api = {
  login: (password) => fetch("/api/login", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ password }) }).then(j),
  getConfig: () => fetch("/api/config").then(j),
  putConfig: (body) => fetch("/api/config", { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(body) }).then(j),
  startRun: (email, domain) => fetch("/api/runs", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ email, domain }) }),
  listRuns: () => fetch("/api/runs").then(j),
  runDetail: (id) => fetch(`/api/runs/${id}`).then(j),
  listAccounts: () => fetch("/api/accounts").then(j),
  rerun: (email) => fetch(`/api/accounts/${encodeURIComponent(email)}/rerun`, { method: "POST" }),
};
```

`web/src/pages/Login.jsx`：密码框 → `api.login` → 成功 `onOk()`。

`web/src/pages/Dashboard.jsx`：
- 「开始注册」按钮（可选后缀 domain / 已有邮箱 email）→ `api.startRun`；若 409 提示「已有任务在运行」。
- 拿到 run_id 后 `new EventSource(\`/api/runs/${id}/stream\`)`，监听 `log` 事件 append 到日志面板，`done` 事件停止并刷新历史。
- 运行历史列表（`api.listRuns`，点某行 `api.runDetail` 看全文日志 + 截图 `<img src="/runs/{id}/{name}">`）。
- 账号列表（`api.listAccounts`，每行「重跑」→ `api.rerun`）。

`web/src/pages/Settings.jsx`：`api.getConfig` 填表单（密码/密钥显示 `••••` 占位），保存 `api.putConfig`（未改的脱敏字段不回传或回传空表示不改）。

`web/src/App.jsx`：极简路由（useState 切 Login/Dashboard/Settings；`getConfig` 401 → 显示 Login）。

`web/src/main.jsx`：挂载 App。

样式内联或一个 `web/src/style.css`，简洁后台风即可。

- [ ] **Step 3: 构建验收**

Run:
```bash
cd web && bun install && bun run build
```
Expected: 生成 `web/dist/index.html` 等静态资源，无构建错误。

（遵守 Global Constraints：bun 用于构建，此步在本地/CI 执行，不在线上机器。）

- [ ] **Step 4: 手动冒烟（可选，有 AnyMail 凭证时）**

在 `config.yaml` 填好 anymail 后 `uv run python serve.py`，浏览器开 `http://localhost:8790` 登录 → 点开始注册 → 看实时日志。

- [ ] **Step 5: Commit**

```bash
git add web/ && git commit -m "feat(web): React 前端（登录/主面板实时日志/设置）"
```

---

### Task 10: Docker 多阶段 + compose + GitHub Actions

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `.dockerignore`
- Create: `.github/workflows/build.yml`
- Modify: `README.md`（安装/部署改为 config.yaml + docker）

**Interfaces:**
- Produces: 镜像 `ghcr.io/yiranxiaohui/claude-register`，`CMD` 起 `serve.py`，暴露 8790，挂 `/app/data` 卷。

- [ ] **Step 1: Dockerfile**

```dockerfile
# 1) 前端
FROM oven/bun:1 AS web
WORKDIR /web
COPY web/package.json ./
RUN bun install
COPY web/ ./
RUN bun run build

# 2) 运行镜像（含 Python + Xvfb + Camoufox）
FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb libgtk-3-0 libx11-xcb1 libasound2 libdbus-glib-1-2 && \
    rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY claude_register/ ./claude_register/
COPY server/ ./server/
COPY serve.py main.py ./
COPY --from=web /web/dist ./web/dist
RUN uv run camoufox fetch
EXPOSE 8790
CMD ["uv", "run", "python", "serve.py"]
```

- [ ] **Step 2: compose + dockerignore**

`docker-compose.yml`：

```yaml
services:
  claude-register:
    image: ghcr.io/yiranxiaohui/claude-register:latest
    restart: unless-stopped
    ports: ["8790:8790"]
    volumes:
      - ./data:/app/data
      - ./config.yaml:/app/config.yaml
```

`.dockerignore`：

```
data/
output/
web/node_modules/
web/dist/
.venv/
__pycache__/
*.pyc
.git/
```

- [ ] **Step 3: GitHub Actions**

`.github/workflows/build.yml`：

```yaml
name: build
on:
  push:
    branches: [main]
    tags: ["v*"]
  workflow_dispatch:
jobs:
  build:
    runs-on: self-hosted
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - uses: docker/build-push-action@v6
        with:
          context: .
          push: true
          tags: |
            ghcr.io/yiranxiaohui/claude-register:latest
            ghcr.io/yiranxiaohui/claude-register:${{ github.sha }}
```

- [ ] **Step 4: 更新 README**

README 安装/部署段改为：复制 `config.example.yaml` 为 `config.yaml` 填 AnyMail + 面板密码；`docker compose pull && docker compose up -d`；浏览器开 `:8790`。保留 CLI 用法说明并注明「env 仍兼容，推荐 config.yaml」。

- [ ] **Step 5: Commit**

```bash
git add Dockerfile docker-compose.yml .dockerignore .github/workflows/build.yml README.md
git commit -m "build: Docker 多阶段 + compose + CI 推 ghcr（Web 面板部署）"
```

---

## 验收

- `uv run pytest tests/ -v` 全绿（config_store / flow_config / console_sink / db / runner / auth / app / serve_smoke）。
- `cd web && bun run build` 产出 dist。
- CLI 无回归：`uv run main.py --help` 正常。
- 部署后浏览器 `:8790`：登录 → 触发注册 → 实时日志滚动 → 历史/账号可见 → 设置可改并脱敏。
