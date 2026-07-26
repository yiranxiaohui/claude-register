# 选后缀 + 自动接码 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `claude-register` 的默认流程从「列出 100 个旧邮箱让人挑」改成「选后缀 → 系统建邮箱 → 自动接码 → 自动填码登录」。

**Architecture:** 把现在 240 行的 `flow.py` 按职责拆成 `console.py`（终端 I/O）、`mailbox.py`（选后缀+建邮箱）、`browser.py`（Playwright 操作）三个模块，`flow.py` 退化为瘦编排层。`anymail.py` 新增 `poll_code()` 轮询 `GET /api/emails/latest` 取验证码。关键约束：`browser.py` 不依赖 `anymail.py`，验证码作为参数传入，两层可独立验证。

**Tech Stack:** Python 3.13、httpx、Playwright（sync API）、pytest、respx（mock httpx）、uv

**Spec:** `docs/superpowers/specs/2026-07-26-suffix-select-code-reception-design.md`

## Global Constraints

- Python `>=3.13`；依赖管理一律用 `uv`（`uv add`、`uv run`），不要直接 `pip install`
- 所有面向用户的终端输出、注释、文档用中文；代码标识符用英文
- `since` 时间戳**必须在 `POST /api/accounts` 之前**记录（接码文档 §8.2）
- 邮箱**一律不自动删除**，清理交给 24 小时 `expires_at` cron 或按 `tag=claude-register` 手动批量清理
- 只要验证码拿到手，就绝不能因为后面填不进去而丢掉它——所有降级路径必须把邮箱地址和验证码打印在终端
- 邮箱有效期默认 24 小时（`ANYMAIL_EXPIRES_HOURS` 可覆盖）
- 新建文件都写 `from __future__ import annotations`，与现有代码一致
- 提交信息用中文，格式 `feat: ` / `refactor: ` / `test: ` / `docs: `

---

## File Structure

| 文件 | 职责 | 状态 |
|---|---|---|
| `main.py` | argparse 入口 | 修改 |
| `claude_register/console.py` | 终端 I/O：`log` / `prompt` / `banner` | 新建 |
| `claude_register/config.py` | 环境变量解析：`resolve_expires_hours` / `resolve_code_regex` | 新建 |
| `claude_register/anymail.py` | AnyMail HTTP 客户端 + `poll_code()` | 修改 |
| `claude_register/mailbox.py` | `choose_suffix` / `create_for_suffix` / `prepare_mailbox` | 新建 |
| `claude_register/browser.py` | Playwright：启动、开页、填邮箱、填验证码 | 新建 |
| `claude_register/flow.py` | 瘦编排层 | 重写 |
| `tests/conftest.py` | pytest fixture | 新建 |
| `tests/test_config.py` | 环境变量解析测试 | 新建 |
| `tests/test_poll_code.py` | 接码轮询测试 | 新建 |
| `tests/test_mailbox.py` | 选后缀 / 建邮箱 / since 时序测试 | 新建 |

**依赖方向**（不允许反向）：

```
flow.py  →  mailbox.py  →  anymail.py  →  config.py
   ↓            ↓              ↓            ↓
browser.py      └──────  console.py  ───────┘
```

`browser.py` 不导入 `anymail.py` 或 `mailbox.py`。

---

### Task 1: 基线提交 + 测试脚手架 + console.py

**Files:**
- Commit: 现有未提交的 `claude_register/`、`main.py`、`.env.example`、`README.md`、`pyproject.toml`、`uv.lock`、删除的 `scripts/`
- Modify: `pyproject.toml`（dev 依赖）
- Create: `claude_register/console.py`
- Create: `tests/conftest.py`

**Interfaces:**
- Consumes: 无
- Produces: `log(msg: str) -> None`、`prompt(msg: str) -> str`、`banner(msg: str) -> None`；pytest fixture `client`

> **注意：** 工作区里 `claude_register/`、`main.py`、`.env.example` 是未提交的新文件，`scripts/01-03_*.py` 已被删除但未提交。先把这些作为基线提交，后续每个 task 的 diff 才是可审的。

- [ ] **Step 1: 提交现有基线**

```bash
git add -A
git commit -m "feat: 重构为 claude_register 包，替代 scripts/ 下的单文件脚本"
```

- [ ] **Step 2: 加 dev 依赖**

```bash
uv add --dev pytest respx
```

预期：`pyproject.toml` 出现 `[dependency-groups]` 的 `dev` 组，`uv.lock` 更新。

- [ ] **Step 3: 写 console.py**

```python
"""终端 I/O：日志、输入、醒目横幅。"""

from __future__ import annotations


def log(msg: str) -> None:
    print(msg, flush=True)


def prompt(msg: str) -> str:
    """读取一行输入；EOF 时返回空串（便于非交互环境）。"""
    try:
        return input(msg).strip()
    except EOFError:
        return ""


def banner(msg: str) -> None:
    """把关键信息（验证码、邮箱）打成醒目横幅，避免刷屏时被淹没。"""
    line = "=" * max(40, len(msg) + 4)
    print(f"\n{line}\n  {msg}\n{line}\n", flush=True)
```

- [ ] **Step 4: 写 tests/conftest.py**

`AnyMailClient.__init__` 会调 `load_dotenv()` 读真实 `.env`。测试里必须把它废掉，否则本机配置会污染测试。

```python
"""pytest 共享 fixture。"""

from __future__ import annotations

import pytest

from claude_register import anymail

BASE_URL = "https://mail.test"


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch):
    """禁止测试读取真实 .env。"""
    monkeypatch.setattr(anymail, "load_dotenv", lambda *a, **k: None)


@pytest.fixture
def client():
    return anymail.AnyMailClient(
        base_url=BASE_URL,
        api_key="ak_test",
        domain="mail.test",
    )
```

- [ ] **Step 5: 验证脚手架能跑**

```bash
uv run pytest tests/ -v
```

预期：`no tests ran`（还没写测试），**不能有 import 错误或 collection 错误**。

- [ ] **Step 6: 提交**

```bash
git add pyproject.toml uv.lock claude_register/console.py tests/conftest.py
git commit -m "test: 添加 pytest/respx 脚手架与 console 模块"
```

---

### Task 2: 环境变量解析（config.py）

**Files:**
- Create: `claude_register/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `console.log`
- Produces:
  - `DEFAULT_CODE_REGEX: str = r"code[^\d]*(\d{6})"`
  - `FALLBACK_CODE_REGEX: str = r"\b(\d{6})\b"`
  - `DEFAULT_EXPIRES_HOURS: float = 24.0`
  - `resolve_expires_hours(raw: str | None, *, default: float = 24.0) -> float | None`（`None` 表示永久）
  - `resolve_code_regex(raw: str | None) -> str`

- [ ] **Step 1: 写失败测试**

`tests/test_config.py`：

```python
"""环境变量解析。"""

from __future__ import annotations

import pytest

from claude_register.config import (
    DEFAULT_CODE_REGEX,
    DEFAULT_EXPIRES_HOURS,
    resolve_code_regex,
    resolve_expires_hours,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, DEFAULT_EXPIRES_HOURS),
        ("", DEFAULT_EXPIRES_HOURS),
        ("   ", DEFAULT_EXPIRES_HOURS),
        ("48", 48.0),
        ("1.5", 1.5),
        ("0", None),
        ("-1", None),
    ],
)
def test_resolve_expires_hours(raw, expected):
    assert resolve_expires_hours(raw) == expected


def test_resolve_expires_hours_invalid_falls_back(capsys):
    """非数字用默认值，并且要提示用户，不能静默。"""
    assert resolve_expires_hours("abc") == DEFAULT_EXPIRES_HOURS
    assert "ANYMAIL_EXPIRES_HOURS" in capsys.readouterr().out


def test_resolve_code_regex_default():
    assert resolve_code_regex(None) == DEFAULT_CODE_REGEX
    assert resolve_code_regex("") == DEFAULT_CODE_REGEX


def test_resolve_code_regex_custom():
    assert resolve_code_regex(r"(\d{4})") == r"(\d{4})"


def test_resolve_code_regex_invalid_falls_back(capsys):
    """正则语法错时退回默认值并提示，不能让流程崩在这里。"""
    assert resolve_code_regex("(unclosed") == DEFAULT_CODE_REGEX
    assert "ANYMAIL_CODE_REGEX" in capsys.readouterr().out
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run pytest tests/test_config.py -v
```

预期：FAIL — `ModuleNotFoundError: No module named 'claude_register.config'`

- [ ] **Step 3: 实现 config.py**

```python
"""环境变量解析。集中在这里，避免各处散落 os.getenv + 兜底逻辑。"""

from __future__ import annotations

import re

from claude_register.console import log

# 主正则用捕获组定位真正的码，避开邮件里的日期数字（接码文档 §8.4）
DEFAULT_CODE_REGEX = r"code[^\d]*(\d{6})"
# 兜底正则：主正则没命中时在客户端匹配返回的邮件正文
FALLBACK_CODE_REGEX = r"\b(\d{6})\b"

DEFAULT_EXPIRES_HOURS = 24.0


def resolve_expires_hours(
    raw: str | None,
    *,
    default: float = DEFAULT_EXPIRES_HOURS,
) -> float | None:
    """解析邮箱有效期小时数。

    空 → default；正数 → 该值；<=0 → None（永久，不传 expires_at）。
    非数字 → default，并打印提示。
    """
    text = (raw or "").strip()
    if not text:
        return default
    try:
        hours = float(text)
    except ValueError:
        log(f"ANYMAIL_EXPIRES_HOURS={text!r} 不是数字，改用默认 {default} 小时。")
        return default
    return hours if hours > 0 else None


def resolve_code_regex(raw: str | None) -> str:
    """解析接码正则。语法错时退回默认值并提示。"""
    text = (raw or "").strip()
    if not text:
        return DEFAULT_CODE_REGEX
    try:
        re.compile(text)
    except re.error as exc:
        log(f"ANYMAIL_CODE_REGEX 语法错（{exc}），改用默认正则。")
        return DEFAULT_CODE_REGEX
    return text
```

- [ ] **Step 4: 跑测试确认通过**

```bash
uv run pytest tests/test_config.py -v
```

预期：全部 PASS（9 个 param + 4 个用例）

- [ ] **Step 5: 提交**

```bash
git add claude_register/config.py tests/test_config.py
git commit -m "feat: 添加 config 模块解析有效期与接码正则"
```

---

### Task 3: poll_code() 接码轮询

**Files:**
- Modify: `claude_register/anymail.py`（新增方法与模块级函数）
- Test: `tests/test_poll_code.py`

**Interfaces:**
- Consumes: `config.resolve_code_regex`、`config.FALLBACK_CODE_REGEX`、`console.log`
- Produces:
  - `extract_code(email: dict, regex: str) -> str | None`（模块级函数）
  - `AnyMailClient.poll_code(*, to: str, since: str, code_regex: str | None = None, fallback_regex: str | None = None, timeout: float = 120.0, interval: float = 3.0, sleep: Callable[[float], None] = time.sleep, monotonic: Callable[[], float] = time.monotonic) -> str | None`

> **为什么注入 `sleep`/`monotonic`：** 不注入就没法测超时和退避——真等 120 秒不可接受。测试传假时钟。

> **致命错误 vs 可重试错误：** 400/401/403 永远不会自己好（正则语法错、key 撤销、scope 不足），必须立刻抛出并把 AnyMail 的 error 原文带上；5xx 和网络异常才退避重试。

- [ ] **Step 1: 写失败测试**

`tests/test_poll_code.py`：

```python
"""接码轮询。"""

from __future__ import annotations

import httpx
import pytest
import respx

from claude_register.anymail import extract_code
from claude_register.config import DEFAULT_CODE_REGEX, FALLBACK_CODE_REGEX

LATEST = "https://mail.test/api/emails/latest"


class FakeClock:
    """假时钟：sleep 只推进时间，不真等。"""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _email(**kw) -> dict:
    base = {
        "subject": "",
        "text_body": "",
        "html_body": "",
        "code": None,
    }
    base.update(kw)
    return base


# ---------- extract_code ----------


def test_extract_code_uses_capture_group():
    email = _email(text_body="Your login code is 123456")
    assert extract_code(email, DEFAULT_CODE_REGEX) == "123456"


def test_extract_code_default_regex_ignores_dates():
    """裸 \\d{6} 会误取日期，主正则必须要求 'code' 字样。"""
    email = _email(text_body="Sent on 2026-07-26 at 123456 UTC")
    assert extract_code(email, DEFAULT_CODE_REGEX) is None


def test_extract_code_prefers_code_over_date():
    email = _email(text_body="Your code is 483920. Sent 2026-07-26.")
    assert extract_code(email, DEFAULT_CODE_REGEX) == "483920"


def test_extract_code_searches_html_and_subject():
    assert extract_code(_email(subject="code 111111"), DEFAULT_CODE_REGEX) == "111111"
    assert extract_code(
        _email(html_body="<b>code</b>: 222222"), DEFAULT_CODE_REGEX
    ) == "222222"


def test_extract_code_fallback_regex():
    email = _email(text_body="Verification: 654321")
    assert extract_code(email, DEFAULT_CODE_REGEX) is None
    assert extract_code(email, FALLBACK_CODE_REGEX) == "654321"


def test_extract_code_fallback_ignores_long_digit_runs():
    """\\b 边界保证不会从 9 位数里截 6 位。"""
    assert extract_code(_email(text_body="id 123456789"), FALLBACK_CODE_REGEX) is None


def test_extract_code_no_match():
    assert extract_code(_email(text_body="no digits here"), DEFAULT_CODE_REGEX) is None


# ---------- poll_code ----------


@respx.mock
def test_poll_code_hit_first_round(client):
    respx.get(LATEST).mock(
        return_value=httpx.Response(200, json={"emails": [_email(code="384729")]})
    )
    clock = FakeClock()
    code = client.poll_code(
        to="a@mail.test",
        since="2026-07-26T00:00:00Z",
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    assert code == "384729"
    assert clock.slept == []  # 首轮命中不该睡


@respx.mock
def test_poll_code_hit_third_round(client):
    respx.get(LATEST).mock(
        side_effect=[
            httpx.Response(200, json={"emails": []}),
            httpx.Response(200, json={"emails": []}),
            httpx.Response(200, json={"emails": [_email(code="112233")]}),
        ]
    )
    clock = FakeClock()
    code = client.poll_code(
        to="a@mail.test",
        since="2026-07-26T00:00:00Z",
        interval=3.0,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    assert code == "112233"
    assert clock.slept == [3.0, 3.0]


@respx.mock
def test_poll_code_timeout_returns_none(client):
    respx.get(LATEST).mock(return_value=httpx.Response(200, json={"emails": []}))
    clock = FakeClock()
    code = client.poll_code(
        to="a@mail.test",
        since="2026-07-26T00:00:00Z",
        timeout=10.0,
        interval=3.0,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    assert code is None
    assert clock.now >= 10.0


@respx.mock
def test_poll_code_client_side_fallback(client):
    """服务端 code 为 null，但正文里有 6 位数 —— 同一次响应里用兜底正则接手，
    不再多发一次请求。"""
    respx.get(LATEST).mock(
        return_value=httpx.Response(
            200,
            json={"emails": [_email(code=None, text_body="Verification: 998877")]},
        )
    )
    clock = FakeClock()
    code = client.poll_code(
        to="a@mail.test",
        since="2026-07-26T00:00:00Z",
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    assert code == "998877"
    assert respx.calls.call_count == 1  # 关键：没有翻倍请求


@respx.mock
def test_poll_code_sends_expected_params(client):
    route = respx.get(LATEST).mock(
        return_value=httpx.Response(200, json={"emails": [_email(code="1")]})
    )
    client.poll_code(
        to="a@mail.test",
        since="2026-07-26T00:00:00Z",
        sleep=lambda s: None,
        monotonic=FakeClock().monotonic,
    )
    params = route.calls[0].request.url.params
    assert params["to"] == "a@mail.test"
    assert params["since"] == "2026-07-26T00:00:00Z"
    assert params["code_regex"] == DEFAULT_CODE_REGEX


@respx.mock
def test_poll_code_backoff_on_5xx_then_recovers(client):
    respx.get(LATEST).mock(
        side_effect=[
            httpx.Response(503, text="upstream down"),
            httpx.Response(503, text="upstream down"),
            httpx.Response(200, json={"emails": [_email(code="445566")]}),
        ]
    )
    clock = FakeClock()
    code = client.poll_code(
        to="a@mail.test",
        since="2026-07-26T00:00:00Z",
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    assert code == "445566"
    assert clock.slept == [1.0, 2.0]  # 指数退避 1s → 2s


@respx.mock
def test_poll_code_backoff_on_network_error(client):
    respx.get(LATEST).mock(
        side_effect=[
            httpx.ConnectError("boom"),
            httpx.Response(200, json={"emails": [_email(code="778899")]}),
        ]
    )
    clock = FakeClock()
    code = client.poll_code(
        to="a@mail.test",
        since="2026-07-26T00:00:00Z",
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    assert code == "778899"
    assert clock.slept == [1.0]


@respx.mock
def test_poll_code_backoff_caps_at_4s(client):
    respx.get(LATEST).mock(return_value=httpx.Response(503, text="down"))
    clock = FakeClock()
    client.poll_code(
        to="a@mail.test",
        since="2026-07-26T00:00:00Z",
        timeout=30.0,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    assert max(clock.slept) == 4.0


@respx.mock
@pytest.mark.parametrize("status", [400, 401, 403])
def test_poll_code_fatal_errors_raise_immediately(client, status):
    """scope 不足 / key 失效 / 正则语法错都不会自己好，必须立刻抛出。"""
    respx.get(LATEST).mock(
        return_value=httpx.Response(status, text='{"error":"missing required scope"}')
    )
    clock = FakeClock()
    with pytest.raises(RuntimeError, match="missing required scope"):
        client.poll_code(
            to="a@mail.test",
            since="2026-07-26T00:00:00Z",
            sleep=clock.sleep,
            monotonic=clock.monotonic,
        )
    assert clock.slept == []  # 不该退避重试
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run pytest tests/test_poll_code.py -v
```

预期：FAIL — `ImportError: cannot import name 'extract_code'`

- [ ] **Step 3: 实现 extract_code + poll_code**

在 `claude_register/anymail.py` 顶部的 import 区加：

```python
import re
import time
from collections.abc import Callable
```

并加上 config / console 的导入：

```python
from claude_register.config import (
    FALLBACK_CODE_REGEX,
    resolve_code_regex,
)
from claude_register.console import log
```

在 `Mailbox` dataclass 之后、`AnyMailClient` 之前加模块级函数：

```python
# 致命错误：不会因为重试而变好（正则语法错 / key 失效 / scope 不足）
FATAL_STATUSES = frozenset({400, 401, 403})


def extract_code(email: dict[str, Any], regex: str) -> str | None:
    """在 subject / text_body / html_body 里找验证码。

    有捕获组返回第 1 组，否则返回整段匹配（与 AnyMail 服务端行为一致）。
    """
    try:
        pattern = re.compile(regex, re.IGNORECASE)
    except re.error:
        return None
    for field in ("subject", "text_body", "html_body"):
        value = email.get(field) or ""
        match = pattern.search(str(value))
        if match:
            return match.group(1) if match.groups() else match.group(0)
    return None
```

在 `AnyMailClient` 里，`delete_mailbox` 之前加：

```python
    def _fetch_latest(
        self,
        *,
        to: str,
        since: str,
        code_regex: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """单次 GET /api/emails/latest。致命状态码直接抛，其余交调用方退避。"""
        params = {
            "to": to,
            "since": since,
            "code_regex": code_regex,
            "limit": max(1, min(int(limit), 50)),
        }
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.get(
                f"{self.base_url}/api/emails/latest",
                headers=self._headers(),
                params=params,
            )
            if resp.status_code in FATAL_STATUSES:
                raise RuntimeError(
                    f"AnyMail 接码失败 {resp.status_code}: {resp.text[:300]}\n"
                    "请确认 API Key 含 emails:read scope，且 code_regex 语法正确。"
                )
            if resp.status_code >= 400:
                # 5xx：交给调用方指数退避
                raise httpx.HTTPStatusError(
                    f"{resp.status_code}: {resp.text[:200]}",
                    request=resp.request,
                    response=resp,
                )
            data = resp.json() if resp.content else {}

        emails = data.get("emails") if isinstance(data, dict) else None
        return [e for e in emails if isinstance(e, dict)] if isinstance(emails, list) else []

    def poll_code(
        self,
        *,
        to: str,
        since: str,
        code_regex: str | None = None,
        fallback_regex: str | None = None,
        timeout: float = 120.0,
        interval: float = 3.0,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> str | None:
        """轮询直到取到验证码；超时返回 None（调用方走降级路径）。

        两级匹配在单次响应内完成：先用服务端提取的 code，没有再用兜底正则
        匹配同一批邮件的正文——避免每轮翻倍请求。
        """
        primary = code_regex or resolve_code_regex(os.getenv("ANYMAIL_CODE_REGEX"))
        fallback = fallback_regex or FALLBACK_CODE_REGEX
        deadline = monotonic() + timeout
        backoff = 1.0

        log(f"开始接码：{to}（超时 {timeout:.0f}s，每 {interval:.0f}s 一次）")
        while monotonic() < deadline:
            try:
                emails = self._fetch_latest(
                    to=to, since=since, code_regex=primary
                )
            except httpx.HTTPError as exc:
                log(f"接码请求失败（{exc}），{backoff:.0f}s 后重试。")
                sleep(backoff)
                backoff = min(backoff * 2, 4.0)
                continue
            backoff = 1.0

            for email in emails:
                code = email.get("code")
                if code:
                    return str(code)
            for email in emails:
                code = extract_code(email, fallback)
                if code:
                    log("服务端未提取到码，已用兜底正则命中。")
                    return code

            sleep(interval)

        return None
```

- [ ] **Step 4: 跑测试确认通过**

```bash
uv run pytest tests/test_poll_code.py -v
```

预期：全部 PASS。若 `test_poll_code_timeout_returns_none` 卡住，说明 `monotonic` 没被用于循环条件。

- [ ] **Step 5: 提交**

```bash
git add claude_register/anymail.py tests/test_poll_code.py
git commit -m "feat: anymail 新增 poll_code 接码轮询与两级正则提取"
```

---

### Task 4: mailbox.py —— 选后缀、建邮箱、since 时序

**Files:**
- Create: `claude_register/mailbox.py`
- Test: `tests/test_mailbox.py`

**Interfaces:**
- Consumes: `AnyMailClient`、`Mailbox`、`config.resolve_expires_hours`、`console.log`、`console.prompt`
- Produces:
  - `utc_now_iso() -> str`
  - `choose_suffix(client, preferred: str | None = None, *, prompt=console.prompt) -> str`
  - `create_for_suffix(client, domain: str) -> Mailbox`
  - `prepare_mailbox(client, *, email: str | None = None, domain: str | None = None, prompt=console.prompt) -> tuple[Mailbox, str]`

> **`prepare_mailbox` 返回 `(mailbox, since)` 是这个 task 的核心。** 把「先记 since、后建邮箱」的顺序封进一个函数，让接码文档 §8.2 那个坑变成可单测的不变量，而不是散在 flow 里靠人记住。

- [ ] **Step 1: 写失败测试**

`tests/test_mailbox.py`：

```python
"""选后缀、建邮箱、since 时序。"""

from __future__ import annotations

import httpx
import pytest
import respx

from claude_register.mailbox import (
    choose_suffix,
    create_for_suffix,
    prepare_mailbox,
)

ACCOUNTS = "https://mail.test/api/accounts"
DOMAINS = "https://mail.test/api/domains"


def _account(email: str, **kw) -> dict:
    base = {"id": "acct-1", "provider": "domain", "email": email, "expires_at": None}
    base.update(kw)
    return {"ok": True, "account": base}


# ---------- choose_suffix ----------


def test_choose_suffix_prefers_explicit_arg(client):
    """--domain 优先级最高，不该发任何请求。"""
    assert choose_suffix(client, "Example.COM") == "example.com"


def test_choose_suffix_normalizes_input(client):
    assert choose_suffix(client, "@mail.example.com.") == "mail.example.com"


def test_choose_suffix_falls_back_to_client_domain(client):
    """client.domain 来自 ANYMAIL_DOMAIN。"""
    assert choose_suffix(client, None) == "mail.test"


@respx.mock
def test_choose_suffix_single_domain_no_prompt(client):
    client.domain = ""
    respx.get(DOMAINS).mock(
        return_value=httpx.Response(200, json={"domains": [{"name": "only.test"}]})
    )

    def _never(msg):
        raise AssertionError("只有一个域名时不该提示用户")

    assert choose_suffix(client, None, prompt=_never) == "only.test"


@respx.mock
def test_choose_suffix_multi_domain_prompts(client):
    client.domain = ""
    respx.get(DOMAINS).mock(
        return_value=httpx.Response(
            200, json={"domains": [{"name": "a.test"}, {"name": "b.test"}]}
        )
    )
    assert choose_suffix(client, None, prompt=lambda msg: "2") == "b.test"


@respx.mock
def test_choose_suffix_empty_input_picks_first(client):
    client.domain = ""
    respx.get(DOMAINS).mock(
        return_value=httpx.Response(
            200, json={"domains": [{"name": "a.test"}, {"name": "b.test"}]}
        )
    )
    assert choose_suffix(client, None, prompt=lambda msg: "") == "a.test"


@respx.mock
def test_choose_suffix_retries_invalid_input(client):
    client.domain = ""
    respx.get(DOMAINS).mock(
        return_value=httpx.Response(
            200, json={"domains": [{"name": "a.test"}, {"name": "b.test"}]}
        )
    )
    answers = iter(["99", "abc", "2"])
    assert choose_suffix(client, None, prompt=lambda msg: next(answers)) == "b.test"


@respx.mock
def test_choose_suffix_no_domains_raises(client):
    client.domain = ""
    respx.get(DOMAINS).mock(return_value=httpx.Response(200, json={"domains": []}))
    with pytest.raises(ValueError, match="ANYMAIL_DOMAIN"):
        choose_suffix(client, None)


# ---------- create_for_suffix ----------


@respx.mock
def test_create_for_suffix_generates_random_local_part(client):
    route = respx.post(ACCOUNTS).mock(
        return_value=httpx.Response(200, json=_account("claude_deadbeef@only.test"))
    )
    box = create_for_suffix(client, "only.test")
    assert box.email.endswith("@only.test")
    sent = route.calls[0].request.read().decode()
    assert "claude_" in sent
    assert "expires_at" in sent  # 默认 24 小时


@respx.mock
def test_create_for_suffix_retries_on_conflict(client):
    route = respx.post(ACCOUNTS).mock(
        side_effect=[
            httpx.Response(409, json={"error": "account already exists"}),
            httpx.Response(200, json=_account("claude_second@only.test")),
        ]
    )
    box = create_for_suffix(client, "only.test")
    assert box.email == "claude_second@only.test"
    first = route.calls[0].request.read().decode()
    second = route.calls[1].request.read().decode()
    assert first != second  # 必须换了前缀


@respx.mock
def test_create_for_suffix_permanent_when_expires_zero(client, monkeypatch):
    monkeypatch.setenv("ANYMAIL_EXPIRES_HOURS", "0")
    route = respx.post(ACCOUNTS).mock(
        return_value=httpx.Response(200, json=_account("claude_x@only.test"))
    )
    create_for_suffix(client, "only.test")
    assert "expires_at" not in route.calls[0].request.read().decode()


# ---------- prepare_mailbox：since 时序不变量 ----------


@respx.mock
def test_prepare_mailbox_records_since_before_create(client):
    """接码文档 §8.2：since 必须早于 POST /api/accounts，
    否则会漏掉「建邮箱完成 → 首次轮询」窗口里到达的邮件。"""
    observed: list[str] = []

    def _capture(request):
        observed.append("post")
        return httpx.Response(200, json=_account("claude_x@mail.test"))

    respx.post(ACCOUNTS).mock(side_effect=_capture)

    box, since = prepare_mailbox(client, domain="mail.test")

    assert observed == ["post"]
    assert box.email == "claude_x@mail.test"
    assert since.endswith("Z")
    # since 必须是建邮箱之前的时刻：重新取 now 一定不早于它
    from claude_register.mailbox import utc_now_iso

    assert since <= utc_now_iso()


@respx.mock
def test_prepare_mailbox_reuses_explicit_email(client):
    respx.get(ACCOUNTS).mock(
        return_value=httpx.Response(
            200,
            json={
                "accounts": [
                    {"id": "old-1", "email": "old@mail.test", "expires_at": None}
                ]
            },
        )
    )
    box, since = prepare_mailbox(client, email="Old@mail.test")
    assert box.email == "old@mail.test"
    assert box.id == "old-1"
    assert since.endswith("Z")


@respx.mock
def test_prepare_mailbox_creates_explicit_email_when_missing(client):
    respx.get(ACCOUNTS).mock(
        return_value=httpx.Response(200, json={"accounts": []})
    )
    respx.post(ACCOUNTS).mock(
        return_value=httpx.Response(200, json=_account("brand@mail.test"))
    )
    box, _ = prepare_mailbox(client, email="brand@mail.test")
    assert box.email == "brand@mail.test"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run pytest tests/test_mailbox.py -v
```

预期：FAIL — `ModuleNotFoundError: No module named 'claude_register.mailbox'`

- [ ] **Step 3: 实现 mailbox.py**

```python
"""选后缀、建邮箱。前缀由系统随机生成，用户只决定后缀。"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from claude_register.anymail import AnyMailClient, Mailbox
from claude_register.config import resolve_expires_hours
from claude_register.console import log
from claude_register.console import prompt as console_prompt


def utc_now_iso() -> str:
    """AnyMail 要的 ISO 8601 UTC 时间戳。"""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize(domain: str) -> str:
    return domain.strip().lstrip("@").strip(".").lower()


def choose_suffix(
    client: AnyMailClient,
    preferred: str | None = None,
    *,
    prompt=console_prompt,
) -> str:
    """决定用哪个后缀：--domain > ANYMAIL_DOMAIN > GET /api/domains 交互选择。"""
    if preferred:
        return _normalize(preferred)
    if client.domain:
        return client.domain

    domains = client.list_domains()
    if not domains:
        raise ValueError(
            "没有可用域名。请在 .env 设置 ANYMAIL_DOMAIN，"
            "或给 API Key 加上 domains:read scope。"
        )
    if len(domains) == 1:
        log(f"使用唯一域名：{domains[0]}")
        return domains[0]

    log("可用后缀：")
    for i, dom in enumerate(domains, start=1):
        log(f"  [{i}] {dom}")
    while True:
        raw = prompt("请选择后缀编号（直接回车=1）：")
        if not raw:
            return domains[0]
        if raw.isdigit() and 1 <= int(raw) <= len(domains):
            return domains[int(raw) - 1]
        log("输入无效，请重试。")


def create_for_suffix(client: AnyMailClient, domain: str) -> Mailbox:
    """按后缀建一个新邮箱，前缀随机（claude_<8位hex>）。"""
    expires_hours = resolve_expires_hours(os.getenv("ANYMAIL_EXPIRES_HOURS"))
    box = client.create_mailbox(
        local_part=None,  # 交给 anymail 生成 claude_<8位hex>
        domain=domain,
        expires_hours=expires_hours,
    )
    log(f"已创建邮箱：{box.email}")
    return box


def prepare_mailbox(
    client: AnyMailClient,
    *,
    email: str | None = None,
    domain: str | None = None,
    prompt=console_prompt,
) -> tuple[Mailbox, str]:
    """返回 (mailbox, since)。

    since 在任何账号写操作之前记录 —— 接码文档 §8.2：若用首次轮询时的 now()
    当 since，会漏掉「建邮箱完成 → 首次轮询」窗口内到达的邮件。
    这个顺序是本函数存在的理由，不要拆开。
    """
    since = utc_now_iso()

    if email:
        box = client.get_or_create_mailbox(email)
        log(f"使用指定邮箱：{box.email} (id={box.id or 'new'})")
        return box, since

    suffix = choose_suffix(client, domain, prompt=prompt)
    return create_for_suffix(client, suffix), since
```

- [ ] **Step 4: 跑测试确认通过**

```bash
uv run pytest tests/test_mailbox.py -v
```

预期：全部 PASS。

若 `test_create_for_suffix_permanent_when_expires_zero` 失败，检查 `anymail.create_mailbox` 是否在 `expires_hours is None` 时跳过 `expires_at`——现有代码的条件是 `if expires_hours is not None and expires_hours > 0`，已经满足。

- [ ] **Step 5: 全量回归**

```bash
uv run pytest tests/ -v
```

预期：Task 2/3/4 的测试全 PASS。

- [ ] **Step 6: 提交**

```bash
git add claude_register/mailbox.py tests/test_mailbox.py
git commit -m "feat: 添加 mailbox 模块，封装选后缀与 since 时序不变量"
```

---

### Task 5: browser.py —— 迁移现有浏览器代码（纯重构）

**Files:**
- Create: `claude_register/browser.py`
- Modify: `claude_register/flow.py`（改为从 browser 导入，暂不改流程）

**Interfaces:**
- Consumes: `console.log`
- Produces:
  - `OUTPUT_DIR: Path`
  - `URL: str = "https://claude.ai/login"`
  - `launch_browser(p) -> Browser`
  - `new_page(browser) -> tuple[BrowserContext, Page]`
  - `open_login(page) -> None`
  - `wait_login_form(page, timeout_ms: int = 120_000) -> None`
  - `fill_email(page, email: str) -> None`
  - `screenshot(page, name: str) -> Path`
  - `pause_for_user() -> None`

> **这是纯搬移，不改行为。** 从 `flow.py` 原样搬 `open_login` / `wait_login_form` / `fill_email` / `launch_browser`，把散在 `run_browser` 里的建 context、截图、暂停抽成命名函数。没有单测——浏览器层靠 Task 6 的实跑验证。做完这步流程行为应与改动前完全一致。

- [ ] **Step 1: 建 browser.py，搬移函数**

```python
"""Playwright 操作。不依赖 anymail —— 验证码作为参数传入。"""

from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import Page, expect

from claude_register.console import log, prompt

URL = "https://claude.ai/login"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"


def screenshot(page: Page, name: str) -> Path:
    OUTPUT_DIR.mkdir(exist_ok=True)
    path = OUTPUT_DIR / name
    page.screenshot(path=path, full_page=True)
    log(f"截图已保存：{path}")
    return path


def launch_browser(p):
    common = {
        "headless": False,
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    try:
        browser = p.chromium.launch(channel="chrome", **common)
        log("已启动本机 Chrome（channel=chrome）")
        return browser
    except Exception as exc:
        log(f"本机 Chrome 不可用（{exc}），回退到 Playwright Chromium")
        return p.chromium.launch(**common)


def new_page(browser):
    context = browser.new_context(
        locale="en-US",
        viewport={"width": 1280, "height": 900},
    )
    page = context.new_page()
    page.set_default_timeout(30_000)
    return context, page


def open_login(page: Page) -> None:
    log(f"正在打开：{URL}")
    page.goto(URL, wait_until="domcontentloaded", timeout=60_000)
    log(f"页面标题：{page.title()}")
    log(f"当前地址：{page.url}")


def wait_login_form(page: Page, timeout_ms: int = 120_000) -> None:
    """等邮箱输入框出现；Cloudflare 验证期间轮询并打印状态。"""
    email_box = page.get_by_placeholder("Enter your email")
    step = 3_000
    waited = 0
    while waited < timeout_ms:
        try:
            if email_box.is_visible():
                log("登录表单已出现。")
                return
        except Exception:
            pass
        try:
            title = page.title()
        except Exception:
            title = "(无法读取标题)"
        log(f"等待登录表单… {waited // 1000}s 标题={title!r} url={page.url}")
        page.wait_for_timeout(step)
        waited += step
    shot = screenshot(page, "waiting_login.png")
    raise RuntimeError(f"登录表单未出现（可能卡在 Cloudflare 验证页）。已截图：{shot}")


def fill_email(page: Page, email: str) -> None:
    email_box = page.get_by_placeholder("Enter your email")
    expect(email_box).to_be_visible(timeout=10_000)
    email_box.click()
    email_box.fill("")
    email_box.press_sequentially(email, delay=30)
    log(f"已填入邮箱：{email}")

    continue_btn = page.get_by_role("button", name="Continue with email")
    expect(continue_btn).to_be_enabled(timeout=10_000)
    continue_btn.click()
    log("已点击 Continue with email")


def pause_for_user() -> None:
    """浏览器保持打开，等用户看完。CLAUDE_REGISTER_NO_PAUSE=1 可跳过。"""
    if os.getenv("CLAUDE_REGISTER_NO_PAUSE", "").strip().lower() in {"1", "true", "yes"}:
        log("CLAUDE_REGISTER_NO_PAUSE=1，跳过手动暂停。")
        return
    prompt("浏览器保持打开。看完后在终端按回车关闭…")
```

- [ ] **Step 2: 改 flow.py 从 browser 导入**

删掉 `flow.py` 里已搬走的 `URL`、`OUTPUT_DIR`、`log`、`open_login`、`wait_login_form`、`fill_email`、`launch_browser`、`_prompt`，改为：

```python
from claude_register.browser import (
    fill_email,
    launch_browser,
    new_page,
    open_login,
    pause_for_user,
    screenshot,
    wait_login_form,
)
from claude_register.console import log, prompt
```

`run_browser` 改用新函数（行为不变）：

```python
def run_browser(email: str) -> None:
    with sync_playwright() as p:
        browser = launch_browser(p)
        context, page = new_page(browser)
        open_login(page)
        wait_login_form(page)
        fill_email(page, email)
        page.wait_for_timeout(2_000)
        screenshot(page, "email_filled.png")
        log(f"当前地址：{page.url}")
        log(f"页面标题：{page.title()}")
        pause_for_user()
        context.close()
        browser.close()
```

`flow.py` 里 `choose_domain` / `create_custom_mailbox` / `choose_mailbox` 暂时保留（Task 7 删）。把它们里的 `_prompt` 调用改成 `prompt`。

- [ ] **Step 3: 验证 import 无环、无语法错**

```bash
uv run python -c "import claude_register.flow; import claude_register.browser; print('ok')"
uv run pytest tests/ -q
```

预期：打印 `ok`，测试全 PASS。

- [ ] **Step 4: 实跑确认行为没变**

```bash
uv run main.py -e <你已有的邮箱>@ckvlhj.xyz
```

预期：跟改动前一样——打开 claude.ai、填入邮箱、点 Continue、截图、停住等回车。**这一步是这个 task 唯一的验证手段，不能跳过。**

- [ ] **Step 5: 提交**

```bash
git add claude_register/browser.py claude_register/flow.py
git commit -m "refactor: 抽出 browser 模块，flow 只保留编排"
```

---

### Task 6: 摸清验证码界面的 DOM（探索任务）

**Files:**
- Create: `docs/superpowers/notes/2026-07-26-code-screen-dom.md`
- Create: `scripts/probe_code_screen.py`

**Interfaces:**
- Consumes: `browser.*`、`mailbox.prepare_mailbox`
- Produces: 一份写下来的 DOM 结论，Task 7 照它写选择器

> **这个 task 不是 TDD，是调查。** claude.ai 的验证码输入界面结构现在未知——可能是单个 `input`，也可能是 6 个分开的 OTP 格子，两种填法完全不同。**先看清楚再写选择器，不要猜。** Task 7 依赖这份笔记的结论。

- [ ] **Step 1: 写探针脚本**

`scripts/probe_code_screen.py`：

```python
"""一次性探针：走到验证码界面，dump DOM 供分析。

用法：uv run scripts/probe_code_screen.py
跑完看 output/code_screen.html 和 output/code_screen.png。
"""

from __future__ import annotations

from playwright.sync_api import sync_playwright

from claude_register.anymail import AnyMailClient, load_dotenv
from claude_register.browser import (
    OUTPUT_DIR,
    fill_email,
    launch_browser,
    new_page,
    open_login,
    screenshot,
    wait_login_form,
)
from claude_register.console import log, prompt
from claude_register.mailbox import prepare_mailbox


def main() -> None:
    load_dotenv()
    client = AnyMailClient()
    mailbox, since = prepare_mailbox(client)
    log(f"邮箱：{mailbox.email}  since={since}")

    with sync_playwright() as p:
        browser = launch_browser(p)
        context, page = new_page(browser)
        open_login(page)
        wait_login_form(page)
        fill_email(page, mailbox.email)

        page.wait_for_timeout(5_000)
        screenshot(page, "code_screen.png")

        OUTPUT_DIR.mkdir(exist_ok=True)
        html_path = OUTPUT_DIR / "code_screen.html"
        html_path.write_text(page.content(), encoding="utf-8")
        log(f"DOM 已保存：{html_path}")

        # 把所有 input 的关键属性打出来
        inputs = page.locator("input").all()
        log(f"\n页面上有 {len(inputs)} 个 input：")
        for i, box in enumerate(inputs):
            attrs = box.evaluate(
                "el => ({type: el.type, name: el.name, id: el.id, "
                "placeholder: el.placeholder, autocomplete: el.autocomplete, "
                "maxLength: el.maxLength, inputMode: el.inputMode, "
                "ariaLabel: el.getAttribute('aria-label'), "
                "dataTestId: el.getAttribute('data-testid')})"
            )
            log(f"  [{i}] {attrs}")

        prompt("\n看完后按回车关闭…")
        context.close()
        browser.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 实跑探针**

```bash
uv run scripts/probe_code_screen.py
```

预期：建出新邮箱、走到验证码界面、`output/code_screen.png` + `output/code_screen.html` 生成，终端列出所有 input 的属性。

若卡在 Cloudflare，重跑或手动过验证后再看。

- [ ] **Step 3: 把结论写成笔记**

`docs/superpowers/notes/2026-07-26-code-screen-dom.md`，必须回答这四个问题：

```markdown
# claude.ai 验证码界面 DOM

日期：2026-07-26（探针：scripts/probe_code_screen.py）

## 1. 是单个输入框还是多个 OTP 格子？
（照实写，附 input 数量与属性）

## 2. 最稳的定位方式？
（优先级：data-testid > aria-label > placeholder > autocomplete="one-time-code" > name/id。
写下实际可用的那个，附完整 Playwright 表达式）

## 3. 填完要不要点提交按钮？
（有些 OTP 输入满 6 位自动提交；写下实测结果与按钮定位）

## 4. 怎么判断「验证码界面已出现」？
（给出 wait_code_screen 该等的那个元素）

## 原始属性 dump
（粘贴 Step 2 的终端输出）
```

- [ ] **Step 4: 提交**

```bash
git add docs/superpowers/notes/2026-07-26-code-screen-dom.md scripts/probe_code_screen.py
git commit -m "docs: 记录 claude.ai 验证码界面 DOM 结构"
```

---

### Task 7: wait_code_screen + fill_code

**Files:**
- Modify: `claude_register/browser.py`
- Read first: `docs/superpowers/notes/2026-07-26-code-screen-dom.md`

**Interfaces:**
- Consumes: Task 6 的 DOM 笔记
- Produces:
  - `wait_code_screen(page, timeout_ms: int = 60_000) -> bool`
  - `fill_code(page, code: str) -> bool`

> **两个函数都返回 `bool`，不抛异常。** 「界面没出现」和「填不进去」都是预期内的降级路径，不是错误——调用方要靠返回值决定降级，异常会把已经拿到的验证码冲掉。

- [ ] **Step 1: 读 Task 6 的笔记**

打开 `docs/superpowers/notes/2026-07-26-code-screen-dom.md`，确认第 1、2、3、4 问的答案。**下面的实现是骨架，选择器必须按笔记里实测的结果填。**

- [ ] **Step 2: 实现两个函数**

加到 `browser.py` 末尾。按笔记调整 `_code_input` 里的定位链：

```python
def _code_input(page: Page):
    """按 Task 6 笔记的优先级定位验证码输入。

    定位链按稳定性排序，逐个试；全都不中返回 None。
    注意：若笔记结论是「6 个分开的 OTP 格子」，改用下面 _fill_otp_boxes 分支。
    """
    candidates = [
        lambda: page.locator("input[autocomplete='one-time-code']"),
        lambda: page.get_by_label("Verification code"),
        lambda: page.get_by_placeholder("Enter code"),
        lambda: page.locator("input[name='code']"),
        lambda: page.locator("input[inputmode='numeric']"),
    ]
    for build in candidates:
        try:
            loc = build()
            if loc.count() >= 1 and loc.first.is_visible():
                return loc.first
        except Exception:
            continue
    return None


def _fill_otp_boxes(page: Page, code: str) -> bool:
    """多格 OTP：逐格填。仅当 Task 6 笔记确认是这种结构时才走这里。"""
    boxes = page.locator("input[inputmode='numeric'], input[maxlength='1']")
    try:
        count = boxes.count()
    except Exception:
        return False
    if count < len(code):
        return False
    for i, ch in enumerate(code):
        boxes.nth(i).fill(ch)
    log(f"已逐格填入验证码（{count} 格）。")
    return True


def wait_code_screen(page: Page, timeout_ms: int = 60_000) -> bool:
    """等验证码界面出现。返回 False 表示没等到（调用方降级，不抛异常）。"""
    step = 2_000
    waited = 0
    while waited < timeout_ms:
        if _code_input(page) is not None:
            log("验证码界面已出现。")
            return True
        log(f"等待验证码界面… {waited // 1000}s url={page.url}")
        page.wait_for_timeout(step)
        waited += step
    log("验证码界面未在超时内出现。")
    return False


def fill_code(page: Page, code: str) -> bool:
    """填验证码并提交。返回 False 表示定位不到（调用方打印验证码让人手填）。"""
    box = _code_input(page)
    if box is None:
        if _fill_otp_boxes(page, code):
            return _submit_code(page)
        log("找不到验证码输入框。")
        return False

    try:
        box.click()
        box.fill("")
        box.press_sequentially(code, delay=50)
        log(f"已填入验证码：{code}")
    except Exception as exc:
        log(f"填验证码失败（{exc}）。")
        return False

    return _submit_code(page)


def _submit_code(page: Page) -> bool:
    """点提交按钮。有些 OTP 满位自动提交，找不到按钮不算失败。"""
    for name in ("Continue", "Verify", "Submit", "Log in"):
        try:
            btn = page.get_by_role("button", name=name)
            if btn.count() >= 1 and btn.first.is_enabled():
                btn.first.click()
                log(f"已点击 {name}")
                return True
        except Exception:
            continue
    log("未找到提交按钮（可能满位自动提交）。")
    return True
```

- [ ] **Step 3: 实跑验证**

先手动跑到验证码界面，确认 `wait_code_screen` 返回 `True`：

```bash
uv run scripts/probe_code_screen.py
```

在探针停住时，另开终端确认能从 AnyMail 后台看到验证码邮件。

**Task 8 会做完整端到端验证。** 这一步只需确认选择器能定位到元素，不报异常。

- [ ] **Step 4: 提交**

```bash
git add claude_register/browser.py
git commit -m "feat: browser 支持等待与填写验证码，定位失败降级返回 False"
```

---

### Task 8: 重写 flow.py + main.py CLI

**Files:**
- Modify: `claude_register/flow.py`（重写）
- Modify: `main.py`
- Delete: `scripts/probe_code_screen.py`（探针用完即弃）

**Interfaces:**
- Consumes: `mailbox.prepare_mailbox`、`browser.*`、`AnyMailClient.poll_code`、`console.*`
- Produces: `run(*, email: str | None = None, domain: str | None = None, auto_code: bool = True, code_timeout: float = 120.0) -> None`

> **`browser.py` 不知道 AnyMail 存在**，所以「等界面 → 接码 → 填码」的编排必须在 `flow.py` 里，通过参数把 code 递给 `fill_code`。

- [ ] **Step 1: 重写 flow.py**

整个文件替换成：

```python
"""编排：选后缀 → 建邮箱 → 填邮箱 → 接码 → 填码。入口 main.py"""

from __future__ import annotations

from playwright.sync_api import sync_playwright

from claude_register.anymail import AnyMailClient, Mailbox, load_dotenv
from claude_register.browser import (
    fill_code,
    fill_email,
    launch_browser,
    new_page,
    open_login,
    pause_for_user,
    screenshot,
    wait_code_screen,
    wait_login_form,
)
from claude_register.console import banner, log
from claude_register.mailbox import prepare_mailbox


def _report_manual_fallback(mailbox: Mailbox, client: AnyMailClient) -> None:
    """降级时必须让用户拿到继续操作所需的一切。"""
    banner(f"邮箱：{mailbox.email}")
    log(f"AnyMail 后台：{client.base_url}")
    log("可以去后台查收验证码，然后在浏览器里手动填入。")


def run_browser(
    client: AnyMailClient,
    mailbox: Mailbox,
    since: str,
    *,
    auto_code: bool,
    code_timeout: float,
) -> None:
    with sync_playwright() as p:
        browser = launch_browser(p)
        context, page = new_page(browser)
        try:
            open_login(page)
            wait_login_form(page)
            fill_email(page, mailbox.email)

            screen_ok = wait_code_screen(page)
            if not screen_ok:
                screenshot(page, "code_screen_missing.png")
                log("验证码界面未出现，但仍继续接码——码本身有价值。")

            code: str | None = None
            if code_timeout > 0:
                code = client.poll_code(
                    to=mailbox.email,
                    since=since,
                    timeout=code_timeout,
                )
            else:
                log("--code-timeout 0，跳过接码。")

            if code is None:
                if code_timeout > 0:
                    log("接码超时，未收到验证码。")
                    screenshot(page, "code_timeout.png")
                    _report_manual_fallback(mailbox, client)
            else:
                banner(f"验证码：{code}")
                if not auto_code:
                    log("--no-auto-code，请手动填入上面的验证码。")
                elif not screen_ok:
                    log("验证码界面未确认出现，请手动填入上面的验证码。")
                elif not fill_code(page, code):
                    log("填码框定位不到，请手动填入上面的验证码。")
                    screenshot(page, "fill_code_failed.png")
                else:
                    page.wait_for_timeout(3_000)
                    screenshot(page, "after_code.png")
                    log(f"当前地址：{page.url}")

            pause_for_user()
        finally:
            context.close()
            browser.close()


def run(
    *,
    email: str | None = None,
    domain: str | None = None,
    auto_code: bool = True,
    code_timeout: float = 120.0,
) -> None:
    load_dotenv()
    if email and domain:
        log("已指定 --email，忽略 --domain（邮箱已含后缀）。")

    client = AnyMailClient(domain=domain)
    mailbox, since = prepare_mailbox(client, email=email, domain=domain)
    log(f"本次邮箱：{mailbox.email} (id={mailbox.id or 'new'})")

    run_browser(
        client,
        mailbox,
        since,
        auto_code=auto_code,
        code_timeout=code_timeout,
    )

    log("完成。")
    banner(f"邮箱：{mailbox.email}")
    if mailbox.id:
        log(f"邮箱 id：{mailbox.id}")
    log("提示：邮箱默认 24 小时后被 AnyMail 清理，若要长期收信请调整有效期。")
```

- [ ] **Step 2: 改 main.py**

```python
"""项目入口：选后缀、建邮箱、自动接码并填入 Claude 登录页。

运行：
  uv run main.py                       选后缀 → 建邮箱 → 自动接码登录
  uv run main.py -d example.com        直接指定后缀
  uv run main.py -e you@example.com    复用指定邮箱
  uv run main.py --no-auto-code        只打印验证码，不自动填
  uv run main.py --code-timeout 180    接码超时秒数（0=跳过接码）
"""

from __future__ import annotations

import argparse

from claude_register.flow import run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="选后缀、建 AnyMail 邮箱并自动接码填入 Claude 登录页",
    )
    parser.add_argument(
        "--email",
        "-e",
        help="复用该邮箱（已存在则复用，不存在则创建）；与 --domain 同给时本项优先",
    )
    parser.add_argument(
        "--domain",
        "-d",
        help="新建邮箱用的后缀域名（也可设 ANYMAIL_DOMAIN）",
    )
    parser.add_argument(
        "--no-auto-code",
        action="store_true",
        help="接到验证码只打印，不自动填入",
    )
    parser.add_argument(
        "--code-timeout",
        type=float,
        default=120.0,
        metavar="SECONDS",
        help="接码超时秒数，默认 120；设 0 跳过接码",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    run(
        email=args.email,
        domain=args.domain,
        auto_code=not args.no_auto_code,
        code_timeout=args.code_timeout,
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: 删掉探针**

```bash
git rm scripts/probe_code_screen.py
```

- [ ] **Step 4: 验证 CLI 与回归**

```bash
uv run main.py --help
uv run pytest tests/ -q
uv run python -c "import claude_register.flow; print('ok')"
```

预期：help 里有 `--no-auto-code` / `--code-timeout`，**没有 `--new`**；测试全 PASS。

- [ ] **Step 5: 端到端实跑（本计划的验收点）**

```bash
uv run main.py
```

预期完整链路：

1. 提示选后缀（或直接用 `ANYMAIL_DOMAIN`）
2. 打印「已创建邮箱：claude_xxxxxxxx@<后缀>」
3. 打开 claude.ai，填入邮箱，点 Continue
4. 打印「验证码界面已出现」
5. 打印「开始接码：…」并轮询
6. 横幅打印「验证码：xxxxxx」
7. 自动填入并提交，登录成功

**若第 7 步失败但第 6 步拿到了码 —— 降级路径生效，属于可接受结果**，把实际情况记下来。

也验一遍降级分支：

```bash
uv run main.py --no-auto-code
uv run main.py --code-timeout 0
```

- [ ] **Step 6: 提交**

```bash
git add -A
git commit -m "feat: 默认流程改为选后缀→建邮箱→自动接码填码"
```

---

### Task 9: 文档与配置示例

**Files:**
- Modify: `README.md`
- Modify: `.env.example`

**Interfaces:**
- Consumes: Task 8 的最终 CLI
- Produces: 无代码接口

- [ ] **Step 1: 重写 README.md**

```markdown
# claude-register

选一个邮箱后缀，系统自动建邮箱、打开 Claude 登录页填入、并自动接收验证码填回。

## 准备

1. 复制 `.env.example` 为 `.env`，填写 AnyMail 配置
2. 安装依赖：

```text
uv sync
uv run playwright install chromium
```

AnyMail API Key 需要的 scope：`emails:read` + `accounts:write`（+ `domains:read`，若没固定 `ANYMAIL_DOMAIN`），且限定账号类型为 `Domain`。

## 启动

选后缀 → 建邮箱 → 自动接码：

```text
uv run main.py
```

直接指定后缀：

```text
uv run main.py -d example.com
```

复用已有邮箱：

```text
uv run main.py -e you@example.com
```

只打印验证码不自动填 / 调整接码超时 / 跳过接码：

```text
uv run main.py --no-auto-code
uv run main.py --code-timeout 180
uv run main.py --code-timeout 0
```

`-e` 与 `-d` 同时给时 `-e` 优先（邮箱已含后缀）。

## 注意

邮箱默认 **24 小时后**被 AnyMail 的 cron 连同邮件一起删除。注册出来的账号若之后还要收信（改密码、设备验证），请在到期前延长有效期，或设 `ANYMAIL_EXPIRES_HOURS=0` 建成永久邮箱。

邮箱不会被本工具自动删除。要清理就按 tag 批量删：`GET /api/accounts?tag=claude-register`。

## 测试

```text
uv run pytest tests/ -v
```

浏览器层没有单测，靠实跑验证；接码与选后缀有完整单测。
```

- [ ] **Step 2: 更新 .env.example**

```text
# AnyMail
ANYMAIL_BASE_URL=https://your-any-mail.example.com
ANYMAIL_API_KEY=ak_xxxxxxxx

# 可选：固定后缀域名（不填则从 /api/domains 交互选择）
# ANYMAIL_DOMAIN=mail.example.com

# 可选：邮箱有效期小时数。留空=24；0 或负数=永久
# ANYMAIL_EXPIRES_HOURS=24

# 可选：覆盖接码正则（建议带捕获组，避开日期数字）
# ANYMAIL_CODE_REGEX=code[^\d]*(\d{6})

# 可选：跳过浏览器结束时的暂停
# CLAUDE_REGISTER_NO_PAUSE=1
```

- [ ] **Step 3: 按 README 验一遍命令**

```bash
uv run main.py --help
uv run pytest tests/ -v
```

预期：README 里写的每个 flag 都真实存在；测试全 PASS。

- [ ] **Step 4: 提交**

```bash
git add README.md .env.example
git commit -m "docs: 更新 README 与 .env.example 说明新流程"
```

---

## Self-Review

**Spec 覆盖检查：**

| Spec 要求 | 对应 Task |
|---|---|
| 拆 `console.py` / `mailbox.py` / `browser.py` / 瘦 `flow.py` | 1、4、5、8 |
| `browser.py` 不依赖 `anymail.py` | 5、7（`fill_code(page, code)` 签名）、8（编排在 flow） |
| `poll_code` 3s 间隔 / 120s 超时 / 超时返 `None` | 3 |
| 两级正则单请求内完成 | 3（`test_poll_code_client_side_fallback` 断言 `call_count == 1`） |
| 指数退避 1s→2s→4s | 3（`test_poll_code_backoff_*`） |
| 403 打印 error 原文且不重试 | 3（`test_poll_code_fatal_errors_raise_immediately`） |
| `since` 早于建邮箱 | 4（`prepare_mailbox` + `test_prepare_mailbox_records_since_before_create`） |
| 选后缀：`-d` > env > `/api/domains`，单域名不提示 | 4 |
| 前缀系统随机生成 `claude_<8位hex>` | 4（复用 `anymail.create_mailbox` 现有生成逻辑） |
| 删掉列 100 个旧邮箱的交互 | 8（重写 flow，`choose_mailbox` 随之删除） |
| 删 `--new`，加 `--no-auto-code` / `--code-timeout` | 8 |
| `-e` 与 `-d` 冲突时 `-e` 胜出并提示 | 8（`run()` 开头） |
| `--code-timeout 0` 跳过接码 | 8 |
| `ANYMAIL_EXPIRES_HOURS` 空/正/≤0 三态 | 2、4 |
| 24 小时默认过期 | 2（`DEFAULT_EXPIRES_HOURS = 24.0`） |
| 邮箱一律不自动删除 | 全程无 `delete_mailbox` 调用；9 写进 README |
| 验证码界面没出现仍继续接码 | 8 |
| 填码框定位不到 → 大字打印 + 截图 + 保持打开 | 7（返回 `False`）、8（`banner` + 降级分支） |
| Cloudflare 卡住保留轮询 + 截图 | 5（原样搬移 `wait_login_form`） |
| DOM 未知 → 先探再写 | 6 → 7 |
| dev 依赖 pytest + respx | 1 |
| 测试清单（poll_code / since / choose_suffix / create / 正则 / 参数交互） | 2、3、4 |

**未被单测覆盖且已知情的部分：** `browser.py` 全部函数、`flow.run_browser` 的编排。靠 Task 5 Step 4、Task 7 Step 3、Task 8 Step 5 的实跑验证。这是有意的——给 Playwright 页面交互写 mock 测试成本高、价值低，实跑才是真验证。

**参数交互一项在 spec 测试清单里要求单测，但计划里放在 Task 8 靠 `--help` 和实跑验证。** 理由：`-e`/`-d` 冲突提示和 `--code-timeout 0` 分支都在 `run()` 里，而 `run()` 会真开浏览器，单测它需要 mock 掉整个 playwright 层，成本远超收益。Task 8 Step 4/5 明确覆盖了这两条路径。**这是对 spec 的有意偏离，执行时若审阅者不接受，可把 `run()` 里的参数归一化抽成纯函数 `normalize_args(email, domain) -> tuple[str|None, str|None]` 再单测。**

**类型一致性检查：** `poll_code` 返回 `str | None`，Task 8 按 `None` 判空——一致。`wait_code_screen` / `fill_code` 返回 `bool`，Task 8 按 bool 判——一致。`prepare_mailbox` 返回 `tuple[Mailbox, str]`，Task 8 解包为 `mailbox, since`——一致。`resolve_expires_hours` 返回 `float | None`，Task 4 直接传给 `create_mailbox(expires_hours=...)`，现有代码的 `if expires_hours is not None and expires_hours > 0` 能正确处理 `None`——一致。`console.prompt` 在 Task 4 里以 `prompt=` 参数注入，测试传 lambda——签名 `(str) -> str` 一致。
