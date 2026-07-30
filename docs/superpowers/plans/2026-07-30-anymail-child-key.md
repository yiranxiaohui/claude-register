# claude-register 接入 AnyMail 子 key 委派实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每次注册在邮箱建好后派生一把仅 `emails:read`、锁定该邮箱、随邮箱过期的一次性子 key,接码轮询改用子 key,注册成功后子 key 随账号导出(行文本第 5 段),失败则撤销;派生失败一律降级回父 key。

**Architecture:** `anymail.py` 加 `create_child_key`/`delete_key` 两个客户端方法(任何失败都警告返回 None,不抛);`flow.run()` 在 `prepare_mailbox` 后派生子 key 并构造子 key 专用 `AnyMailClient` 传给 `run_browser` 轮询;`accounts.py` 的 `AccountRecord` 加 `mail_key`/`mail_base_url` 字段并把行导出改 5 段。

**Tech Stack:** Python 3 + httpx,测试 pytest + respx(沿用 `tests/conftest.py` 的 `client` fixture,BASE_URL=`https://mail.test`,api_key=`ak_test`)。

**Spec:** `docs/superpowers/specs/2026-07-30-anymail-child-key-design.md`

## Global Constraints

- TDD:先写失败测试再实现。测试命令统一 `uv run pytest`(单测 `uv run pytest tests/test_xxx.py -v`)。
- **父 key 永不进导出**;降级时 `mail_key` 为空串,行导出恒 5 段。
- 派生/回收的一切失败都不得中断注册主流程:`create_child_key` 返回 None,`delete_key` 只警告。
- 日志用项目自己的 `claude_register.console.log`,中文文案与现有风格一致。
- 不跑构建;Python 环境用 uv,不 pip install。
- commit message 结尾必须带:
  ```
  Generated with [Claude Code](https://claude.ai/code)
  via [Happy](https://happy.engineering)

  Co-Authored-By: Claude <noreply@anthropic.com>
  Co-Authored-By: Happy <yesreply@happy.engineering>
  ```

---

### Task 1: AnyMail 客户端派生/回收方法

**Files:**
- Modify: `claude_register/anymail.py`(在 `Mailbox` dataclass 附近加 `ChildKey`;在 `delete_mailbox` 前加两个方法)
- Test: `tests/test_anymail_child_key.py`(新建)

**Interfaces:**
- Produces: `ChildKey`(frozen dataclass,字段 `id: str`、`plaintext: str`);`AnyMailClient.create_child_key(*, email: str, expires_at: str | None, name_prefix: str = "claude-register") -> ChildKey | None`;`AnyMailClient.delete_key(key_id: str) -> None`。Task 3 依赖这三者。

- [ ] **Step 1: 写失败测试**

`tests/test_anymail_child_key.py`:

```python
"""create_child_key / delete_key:派生、降级、回收。"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

KEYS = "https://mail.test/api/keys"


def _key_response(**kw) -> dict:
    base = {
        "id": "kid-1",
        "name": "claude-register a@mail.test",
        "key_prefix": "ak_child12",
        "scopes": ["emails:read"],
        "provider": "domain",
        "address": "a@mail.test",
        "expires_at": "2026-07-31T00:00:00Z",
        "created_by_key_id": "parent-1",
    }
    base.update(kw)
    return {"ok": True, "key": base, "plaintext": "ak_child_secret"}


@respx.mock
def test_create_child_key_success_sends_narrowed_body(client):
    route = respx.post(KEYS).mock(
        return_value=httpx.Response(201, json=_key_response())
    )
    child = client.create_child_key(
        email="A@Mail.Test", expires_at="2026-07-31T00:00:00Z"
    )
    assert child is not None
    assert child.id == "kid-1"
    assert child.plaintext == "ak_child_secret"

    body = json.loads(route.calls.last.request.content)
    assert body["name"] == "claude-register a@mail.test"
    assert body["scopes"] == ["emails:read"]
    assert body["provider"] == "domain"
    assert body["address"] == "a@mail.test"  # 输入已规整为小写
    assert body["expires_at"] == "2026-07-31T00:00:00Z"
    assert route.calls.last.request.headers["Authorization"] == "Bearer ak_test"


@respx.mock
def test_create_child_key_forwards_null_expiry(client):
    """邮箱永久时 expires_at 传 null(由服务端按父 key 约束裁决)。"""
    route = respx.post(KEYS).mock(
        return_value=httpx.Response(201, json=_key_response(expires_at=None))
    )
    child = client.create_child_key(email="a@mail.test", expires_at=None)
    assert child is not None
    body = json.loads(route.calls.last.request.content)
    assert body["expires_at"] is None


@pytest.mark.parametrize("status", [400, 403, 500])
@respx.mock
def test_create_child_key_degrades_on_http_error(client, status):
    """403(缺 keys:create)/400(子集越界)/5xx:统一降级返回 None,不抛。"""
    respx.post(KEYS).mock(
        return_value=httpx.Response(status, json={"error": "nope"})
    )
    assert client.create_child_key(email="a@mail.test", expires_at=None) is None


@respx.mock
def test_create_child_key_degrades_on_network_error(client):
    respx.post(KEYS).mock(side_effect=httpx.ConnectError("boom"))
    assert client.create_child_key(email="a@mail.test", expires_at=None) is None


@respx.mock
def test_create_child_key_degrades_on_malformed_response(client):
    """200 但没有 plaintext/key.id:同样降级,绝不返回半残 ChildKey。"""
    respx.post(KEYS).mock(return_value=httpx.Response(201, json={"ok": True}))
    assert client.create_child_key(email="a@mail.test", expires_at=None) is None


@respx.mock
def test_delete_key_200_and_404_silent(client):
    respx.delete(f"{KEYS}/kid-1").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    client.delete_key("kid-1")
    respx.delete(f"{KEYS}/kid-2").mock(
        return_value=httpx.Response(404, json={"error": "key not found"})
    )
    client.delete_key("kid-2")  # 幂等,不抛


@respx.mock
def test_delete_key_500_warns_not_raises(client):
    respx.delete(f"{KEYS}/kid-3").mock(
        return_value=httpx.Response(500, text="oops")
    )
    client.delete_key("kid-3")  # 只警告


def test_delete_key_empty_id_is_noop(client):
    # respx 未 mock 任何路由:若发了请求会直接报错,借此断言零请求
    client.delete_key("")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_anymail_child_key.py -v`
Expected: FAIL,`AttributeError: ... has no attribute 'create_child_key'`

- [ ] **Step 3: 实现**

`claude_register/anymail.py`,在 `Mailbox` dataclass 之后加:

```python
@dataclass(frozen=True)
class ChildKey:
    """按次派生的受限子 key(仅 emails:read + 锁定单邮箱)。明文只在创建响应可得。"""

    id: str
    plaintext: str
```

在 `delete_mailbox` 之前加两个方法(注意保持类内缩进):

```python
    def create_child_key(
        self,
        *,
        email: str,
        expires_at: str | None,
        name_prefix: str = "claude-register",
    ) -> ChildKey | None:
        """POST /api/keys 派生仅 emails:read、锁定 email、随邮箱过期的子 key。

        任何失败(403 缺 keys:create / 400 子集越界 / 5xx / 网络错误 / 响应异形)
        都只警告并返回 None——派生失败不值得中断注册,调用方降级用父 key 轮询。
        """
        target = email.strip().lower()
        body: dict[str, Any] = {
            "name": f"{name_prefix} {target}",
            "scopes": ["emails:read"],
            "provider": "domain",
            "address": target,
            "expires_at": expires_at,
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(
                    f"{self.base_url}/api/keys",
                    headers=self._headers(content_type=True),
                    json=body,
                )
        except httpx.HTTPError as exc:
            log(f"派生子 key 请求失败({exc}),降级:轮询继续用主 key。")
            return None

        if resp.status_code >= 400:
            log(
                f"派生子 key 失败 {resp.status_code}: {resp.text[:200]}。"
                "降级:轮询继续用主 key,导出的 mailKey 将为空。"
                "(403 通常是主 key 缺 keys:create;400 可能是子集越界,"
                "如主 key 带有效期而邮箱永久。)"
            )
            return None

        try:
            data = resp.json() if resp.content else {}
        except ValueError:
            data = {}
        key = data.get("key") if isinstance(data, dict) else None
        key_id = str(key.get("id") or "") if isinstance(key, dict) else ""
        plaintext = str(data.get("plaintext") or "") if isinstance(data, dict) else ""
        if not key_id or not plaintext:
            log(f"派生子 key 响应异常:{data!r},降级:轮询继续用主 key。")
            return None
        return ChildKey(id=key_id, plaintext=plaintext)

    def delete_key(self, key_id: str) -> None:
        """DELETE /api/keys/{id} 撤销子 key。404 视为已删;其余失败只警告。

        回收失败不影响主流程——子 key 本身带过期兜底。
        """
        if not key_id:
            return
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.delete(
                    f"{self.base_url}/api/keys/{key_id}",
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            log(f"撤销子 key 请求失败({exc}),忽略——子 key 会随过期自动失效。")
            return
        if resp.status_code >= 400 and resp.status_code != 404:
            log(
                f"撤销子 key 失败 {resp.status_code}: {resp.text[:200]},"
                "忽略——子 key 会随过期自动失效。"
            )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_anymail_child_key.py -v`
Expected: 9 项全 PASS

- [ ] **Step 5: Commit**

```bash
git add claude_register/anymail.py tests/test_anymail_child_key.py
git commit -m "feat(anymail): create_child_key/delete_key 派生与回收受限子 key"
```

---

### Task 2: AccountRecord 携带 mail_key 并扩为 5 段导出

**Files:**
- Modify: `claude_register/accounts.py`(`AccountRecord` :17-51)
- Test: `tests/test_accounts.py`(追加)

**Interfaces:**
- Produces: `AccountRecord` 新字段 `mail_key: str = ""`、`mail_base_url: str = ""`;`to_dict()` 恒含这两个键;`line_export()` 返回 `email----password----sessionKey----proxy----mailKey`。Task 3 的 `_capture` 依赖这两个字段名。

- [ ] **Step 1: 写失败测试**

`tests/test_accounts.py` 末尾追加:

```python
# ---------- mail_key 导出(子 key 委派) ----------


def test_line_export_has_five_segments_with_mail_key():
    r = AccountRecord(
        email="a@b.c", password="p", sessionKey="sk", proxy="pr",
        mail_key="ak_child",
    )
    assert r.line_export() == "a@b.c----p----sk----pr----ak_child"


def test_line_export_keeps_five_segments_when_mail_key_empty():
    """降级(没派生成子 key)时段数不变,消费端解析稳定。"""
    r = AccountRecord(email="a@b.c")
    line = r.line_export()
    assert line.split("----") == ["a@b.c", "", "", "", ""]


def test_to_dict_always_contains_mail_fields():
    d = AccountRecord(email="a@b.c").to_dict()
    assert d["mail_key"] == ""
    assert d["mail_base_url"] == ""


def test_to_dict_carries_mail_fields():
    d = AccountRecord(
        email="a@b.c", mail_key="ak_child", mail_base_url="https://mail.test"
    ).to_dict()
    assert d["mail_key"] == "ak_child"
    assert d["mail_base_url"] == "https://mail.test"
```

(若文件顶部未导入 `AccountRecord`,按既有导入风格补。)

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_accounts.py -v`
Expected: 新增 4 项 FAIL(`unexpected keyword argument 'mail_key'`)

- [ ] **Step 3: 实现**

`claude_register/accounts.py`:

1. 模块 docstring 更新为 `"""注册成功后的账号落盘:email / password / sessionKey / proxy / mailKey。"""`
2. `AccountRecord` 在 `mailbox_id` 字段后加:

```python
    mail_key: str = ""       # AnyMail 子 key 明文(仅 emails:read、锁定本邮箱);降级为空
    mail_base_url: str = ""  # 子 key 对应的 AnyMail 服务地址,分享账号时一并给出
```

3. `to_dict()` 的 data 字典中(`"mailbox_id"` 行后)加:

```python
            "mail_key": self.mail_key or "",
            "mail_base_url": self.mail_base_url or "",
```

4. `line_export()` docstring 改为 `"""常见账号导出格式:email----password----sessionKey----proxy----mailKey"""`,列表末尾追加 `self.mail_key or ""`。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_accounts.py -v`
Expected: 全 PASS(既有用例若有对 4 段格式的断言,同步修正其期望——那是本任务的合法改动,修正时保留原测试意图)

- [ ] **Step 5: Commit**

```bash
git add claude_register/accounts.py tests/test_accounts.py
git commit -m "feat(accounts): 账号导出携带 mail_key/mail_base_url,行文本扩为 5 段"
```

---

### Task 3: flow 接线——派生、轮询切换、失败回收、随账号落盘

**Files:**
- Modify: `claude_register/flow.py`(`run_browser` :58-192、`run` :195-257、`_report_manual_fallback` :46-55)
- Test: `tests/test_flow_child_key.py`(新建)

**Interfaces:**
- Consumes: Task 1 的 `ChildKey`/`create_child_key`/`delete_key`;Task 2 的 `AccountRecord(mail_key=, mail_base_url=)`。
- Produces: `run_browser(client, mailbox, since, *, auto_login, code_timeout, proxy=None, password="", poll_client=None, mail_key="")`——`poll_client=None` 时回落用 `client`(兼容既有调用)。

- [ ] **Step 1: 写失败测试**

`tests/test_flow_child_key.py`:

```python
"""flow 子 key 委派接线:派生成功走子 key、失败降级、注册失败回收。"""

from __future__ import annotations

import inspect

import pytest

from claude_register import anymail, flow
from server.config_store import Config


def _cfg() -> Config:
    return Config(
        anymail_api_key="ak_parent",
        anymail_base_url="https://mail.test",
        anymail_domain="mail.test",
    )


def _mailbox() -> anymail.Mailbox:
    return anymail.Mailbox(
        id="m1", email="x@mail.test", expires_at="2026-07-31T00:00:00Z"
    )


def test_run_browser_accepts_poll_client_and_mail_key():
    sig = inspect.signature(flow.run_browser)
    assert "poll_client" in sig.parameters
    assert "mail_key" in sig.parameters
    assert sig.parameters["poll_client"].default is None
    assert sig.parameters["mail_key"].default == ""


@pytest.fixture
def wired(monkeypatch):
    """桩掉外部 IO:建邮箱、浏览器、代理校验;记录派生/回收/轮询走向。"""
    seen: dict = {"deleted": []}

    monkeypatch.setattr(flow, "validate_proxy", lambda proxy: None)
    monkeypatch.setattr(
        flow, "prepare_mailbox",
        lambda client, **kw: (_mailbox(), "2026-07-30T00:00:00Z"),
    )

    def fake_run_browser(client, mailbox, since, *, poll_client=None,
                         mail_key="", **kw):
        seen["poll_key"] = (poll_client or client).api_key
        seen["mail_key"] = mail_key
        return seen.get("browser_result")

    monkeypatch.setattr(flow, "run_browser", fake_run_browser)
    monkeypatch.setattr(
        anymail.AnyMailClient, "delete_key",
        lambda self, key_id: seen["deleted"].append(key_id),
    )
    return seen


def _mint_ok(monkeypatch):
    monkeypatch.setattr(
        anymail.AnyMailClient, "create_child_key",
        lambda self, *, email, expires_at, **kw: anymail.ChildKey(
            id="kid-1", plaintext="ak_child"
        ),
    )


def _mint_fail(monkeypatch):
    monkeypatch.setattr(
        anymail.AnyMailClient, "create_child_key",
        lambda self, *, email, expires_at, **kw: None,
    )


def test_run_polls_with_child_key_and_keeps_it_on_success(wired, monkeypatch):
    _mint_ok(monkeypatch)
    wired["browser_result"] = {"sessionKey": "sk-1"}
    flow.run(config=_cfg())
    assert wired["poll_key"] == "ak_child"
    assert wired["mail_key"] == "ak_child"
    assert wired["deleted"] == []  # 成功:子 key 随账号交付,不回收


def test_run_degrades_to_parent_key_without_export(wired, monkeypatch):
    _mint_fail(monkeypatch)
    wired["browser_result"] = {"sessionKey": "sk-1"}
    flow.run(config=_cfg())
    assert wired["poll_key"] == "ak_parent"
    assert wired["mail_key"] == ""  # 父 key 绝不进导出
    assert wired["deleted"] == []


def test_run_revokes_child_when_registration_fails(wired, monkeypatch):
    _mint_ok(monkeypatch)
    wired["browser_result"] = None  # 没拿到 sessionKey
    flow.run(config=_cfg())
    assert wired["deleted"] == ["kid-1"]


def test_run_revokes_child_when_browser_raises(wired, monkeypatch):
    _mint_ok(monkeypatch)

    def boom(*a, **kw):
        raise RuntimeError("browser exploded")

    monkeypatch.setattr(flow, "run_browser", boom)
    with pytest.raises(RuntimeError):
        flow.run(config=_cfg())
    assert wired["deleted"] == ["kid-1"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_flow_child_key.py -v`
Expected: 签名测试 FAIL(`poll_client` 不在参数里);其余 FAIL 或因 TypeError 失败

- [ ] **Step 3: 实现**

`claude_register/flow.py` 三处改动:

1. **`run_browser` 签名与轮询切换**:

```python
def run_browser(
    client: AnyMailClient,
    mailbox: Mailbox,
    since: str,
    *,
    auto_login: bool,
    code_timeout: float,
    proxy: str | None = None,
    password: str = "",
    poll_client: AnyMailClient | None = None,
    mail_key: str = "",
) -> dict | None:
    """跑浏览器登录/建号。成功拿到 sessionKey 时返回账号 dict,否则 None。

    poll_client:接码轮询用的客户端(子 key);None 时回落用 client(父 key)。
    mail_key:随账号导出的子 key 明文;降级时空串,父 key 绝不写进导出。
    """
    poll = poll_client or client
```

函数体内三处引用改为 `poll`:
- `link = client.poll_magic_link(...)` → `link = poll.poll_magic_link(...)`
- `code = client.poll_code(...)` → `code = poll.poll_code(...)`
- `_report_manual_fallback(mailbox, client)` → `_report_manual_fallback(mailbox, poll)`(若存在此调用;`base_url` 两者相同,仅统一口径)

2. **`_capture` 落盘带上子 key**(`AccountRecord(` 构造处,`mailbox_id` 之后):

```python
            mail_key=mail_key,
            mail_base_url=client.base_url if mail_key else "",
```

3. **`run()` 派生与回收**(`prepare_mailbox(...)` 与 `log(f"本次邮箱...")` 之后、`run_browser` 调用改造):

```python
    child = client.create_child_key(
        email=mailbox.email, expires_at=mailbox.expires_at
    )
    if child:
        poll_client = AnyMailClient(
            base_url=client.base_url,
            api_key=child.plaintext,
            domain=client.domain or None,
            code_regex=client.code_regex or None,
        )
        log("已派生本邮箱专用子 key(仅 emails:read),接码轮询改用子 key。")
    else:
        poll_client = client

    try:
        account = run_browser(
            client,
            mailbox,
            since,
            auto_login=auto_login,
            code_timeout=code_timeout,
            proxy=proxy,
            password=password,
            poll_client=poll_client,
            mail_key=child.plaintext if child else "",
        )
    except BaseException:
        if child:
            client.delete_key(child.id)
            log("注册中断,已撤销本次派生的子 key。")
        raise

    if child and not (account and account.get("sessionKey")):
        client.delete_key(child.id)
        log("注册未成功,已撤销本次派生的子 key。")
```

(原 `account = run_browser(...)` 调用整体被上面替换;其后的收尾日志保持不变。)

- [ ] **Step 4: 跑单测 + 全量**

Run: `uv run pytest tests/test_flow_child_key.py -v`
Expected: 5 项全 PASS

Run: `uv run pytest`
Expected: 全绿(若 `test_flow_config.py` 等对 `run_browser` 签名有约束,新增参数带默认值不应破坏)

- [ ] **Step 5: Commit**

```bash
git add claude_register/flow.py tests/test_flow_child_key.py
git commit -m "feat(flow): 注册按次派生子 key 轮询,失败撤销、成功随账号导出"
```
