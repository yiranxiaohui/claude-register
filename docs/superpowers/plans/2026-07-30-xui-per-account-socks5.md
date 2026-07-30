# 3x-ui 按账号动态开专属 SOCKS5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 注册每个 Claude 账号时，从多台 3x-ui 节点池随机挑一台动态开一个本账号专属的 SOCKS5（独立端口/凭据/inbound 级有效期），绑定账号导出、注册失败即撤销；面板做节点增删改、测试连接、手动清理过期 inbound。

**Architecture:** 新增 `claude_register/xui.py`（单台 3x-ui API 客户端，封装已验证的 login/list/add/del 调用）与 `claude_register/proxy_pool.py`（跨节点开号器：随机挑节点→选空闲端口→建 mixed inbound→返回 socks5 URL + 撤销句柄）。`flow.run` 复刻现有 AnyMail 子 key 的「派生→失败撤销→成功导出」模式接入代理。`server/config_store.py` 扩展出 `xui` 配置段（含 nodes 列表），`server/app.py` 加测试连接/清理接口，`web` 面板加节点管理 UI。

**Tech Stack:** Python 3.13，httpx（同步 Client，自签证书 `verify=False`），FastAPI，pytest + respx（httpx mock）+ monkeypatch，React（Vite + bun）。

## Global Constraints

- Python `>=3.13`；包管理用 **uv**（`uv run pytest`），前端用 **bun**（`bun run build`），不用 npm/yarn/pnpm。
- httpx 调用一律 `verify=False`（3x-ui 自签证书，等价 `curl -k`）。
- 所有 3x-ui 路径基于节点 `base_url`（可能带自定义 base path，如 `.../5XOrf2HJAUEP0gfcPT`），拼接为 `{base_url}{path}`，`base_url` 去尾部 `/`。
- SOCKS5 出口 = `mixed` 协议 inbound，settings 固定为 `{"auth":"password","accounts":[{"user":U,"pass":P}],"udp":true,"ip":"127.0.0.1"}`。
- inbound 有效期只在 inbound 级 `expiryTime`（Unix **毫秒**）；socks account 无按账号到期。
- 代理 URL 格式：`socks5://{user}:{pass}@{proxy_host}:{port}`；`proxy_host` 留空取 `base_url` 主机名。
- 派生/开号点放在 `prepare_mailbox` 之后、`run_browser` 之前，remark 写 `reg:{email}` 便于溯源。
- 失败回收要与现有 `client.delete_key(child.id)` 两处回收点对称（异常路径 + 事后判 sessionKey）。
- 密钥脱敏沿用现有约定：`REDACTED = "••••"`，GET 时脱敏、保存时留空/脱敏值 = 不改。
- 已验证的 3x-ui 响应形状：所有接口返回 `{"success":bool,"msg":str,"obj":...}`；login 下发 `3x-ui` cookie；add 的 `obj.id` 是新 inbound id；del 的 `obj` 是被删 id。

---

### Task 1: XuiClient — 单台 3x-ui API 客户端

**Files:**
- Create: `claude_register/xui.py`
- Test: `tests/test_xui.py`

**Interfaces:**
- Consumes: 无（仅 httpx）。
- Produces:
  - `class XuiError(Exception)`
  - `class XuiClient(base_url: str, username: str, password: str, *, proxy_host: str = "", timeout: float = 30.0)`
  - `XuiClient.proxy_host: str`（构造后确定：入参或从 base_url 解析的 hostname）
  - `XuiClient.login() -> None`（成功后置 `self._cookies`）
  - `XuiClient.list_inbounds() -> list[dict]`
  - `XuiClient.create_socks_inbound(user: str, password: str, port: int, expiry_ms: int, remark: str) -> int`（返回新 inbound id）
  - `XuiClient.delete_inbound(inbound_id: int) -> None`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_xui.py
from __future__ import annotations

import json

import httpx
import pytest
import respx

from claude_register.xui import XuiClient, XuiError

BASE = "https://panel.test:2053/secretpath"


def _login_route(router, *, ok=True):
    return router.post(f"{BASE}/login").mock(
        return_value=httpx.Response(
            200,
            json={"success": ok, "msg": "" if ok else "wrong", "obj": None},
            headers={"set-cookie": "3x-ui=sess-abc; Path=/; HttpOnly"},
        )
    )


def test_proxy_host_defaults_to_base_url_hostname():
    c = XuiClient(BASE, "u", "p")
    assert c.proxy_host == "panel.test"


def test_proxy_host_override_wins():
    c = XuiClient(BASE, "u", "p", proxy_host="node1.example.com")
    assert c.proxy_host == "node1.example.com"


@respx.mock
def test_login_failure_raises():
    _login_route(respx, ok=False)
    with pytest.raises(XuiError):
        XuiClient(BASE, "u", "p").login()


@respx.mock
def test_list_inbounds_returns_obj_list_and_logs_in_first():
    _login_route(respx)
    respx.get(f"{BASE}/panel/api/inbounds/list").mock(
        return_value=httpx.Response(
            200, json={"success": True, "msg": "", "obj": [{"id": 2, "port": 38695}]}
        )
    )
    c = XuiClient(BASE, "u", "p")
    got = c.list_inbounds()
    assert got == [{"id": 2, "port": 38695}]


@respx.mock
def test_create_socks_inbound_posts_mixed_and_returns_id():
    _login_route(respx)
    captured = {}

    def _add(request):
        captured["body"] = dict(httpx.QueryParams(request.content.decode()))
        return httpx.Response(
            200, json={"success": True, "msg": "created", "obj": {"id": 7, "port": 45000}}
        )

    respx.post(f"{BASE}/panel/api/inbounds/add").mock(side_effect=_add)
    c = XuiClient(BASE, "u", "p")
    iid = c.create_socks_inbound("alice", "pw123", 45000, 1785402000000, "reg:x@m.test")
    assert iid == 7
    body = captured["body"]
    assert body["protocol"] == "mixed"
    assert body["port"] == "45000"
    assert body["expiryTime"] == "1785402000000"
    assert body["remark"] == "reg:x@m.test"
    settings = json.loads(body["settings"])
    assert settings["auth"] == "password"
    assert settings["accounts"] == [{"user": "alice", "pass": "pw123"}]


@respx.mock
def test_create_failure_raises_xui_error():
    _login_route(respx)
    respx.post(f"{BASE}/panel/api/inbounds/add").mock(
        return_value=httpx.Response(
            200, json={"success": False, "msg": "port already in use", "obj": None}
        )
    )
    with pytest.raises(XuiError) as ei:
        XuiClient(BASE, "u", "p").create_socks_inbound("a", "b", 45000, 1, "r")
    assert "port" in str(ei.value)


@respx.mock
def test_delete_inbound_hits_del_path():
    _login_route(respx)
    route = respx.post(f"{BASE}/panel/api/inbounds/del/7").mock(
        return_value=httpx.Response(200, json={"success": True, "msg": "", "obj": 7})
    )
    XuiClient(BASE, "u", "p").delete_inbound(7)
    assert route.called


@respx.mock
def test_relogin_on_non_json_session_expiry():
    # 首次列表返回登录页 HTML（会话失效），应重登后重试成功
    login = _login_route(respx)
    calls = {"n": 0}

    def _list(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, text="<html>login</html>")
        return httpx.Response(200, json={"success": True, "msg": "", "obj": []})

    respx.get(f"{BASE}/panel/api/inbounds/list").mock(side_effect=_list)
    c = XuiClient(BASE, "u", "p")
    assert c.list_inbounds() == []
    assert login.call_count == 2  # 初次 + 会话失效重登
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_xui.py -q`
Expected: FAIL（`ModuleNotFoundError: claude_register.xui`）

- [ ] **Step 3: Write minimal implementation**

```python
# claude_register/xui.py
"""单台 3x-ui 面板的 API 客户端。

封装已验证过的调用：POST /login 拿 3x-ui cookie；GET /panel/api/inbounds/list；
POST /panel/api/inbounds/add（mixed 协议 = socks5+http，用 accounts 认证）；
POST /panel/api/inbounds/del/{id}。3x-ui 自签证书，请求一律 verify=False。
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit

import httpx

from claude_register.console import log

# mixed inbound 的固定 sniffing/allocate；socks 场景用不到，给合法空值即可。
_SNIFFING = json.dumps({"enabled": False, "destOverride": []})


class XuiError(Exception):
    """3x-ui 返回 success=false 或响应异形时抛出。"""


class XuiClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        proxy_host: str = "",
        timeout: float = 30.0,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.username = username
        self.password = password
        self.proxy_host = (proxy_host or "").strip() or (urlsplit(self.base_url).hostname or "")
        self.timeout = timeout
        self._cookies: httpx.Cookies | None = None

    def _new_client(self) -> httpx.Client:
        return httpx.Client(timeout=self.timeout, verify=False)

    def login(self) -> None:
        with self._new_client() as c:
            resp = c.post(
                f"{self.base_url}/login",
                data={"username": self.username, "password": self.password},
            )
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError as exc:
            raise XuiError(f"登录响应非 JSON：{resp.text[:120]}") from exc
        if not data.get("success"):
            raise XuiError(f"3x-ui 登录失败：{data.get('msg')!r}")
        self._cookies = resp.cookies

    def _call(self, method: str, path: str, *, data: dict | None = None) -> dict:
        """带 cookie 调 API；会话失效（返回非 JSON/登录页）时自动重登一次。"""
        if self._cookies is None:
            self.login()
        parsed = self._request_json(method, path, data)
        if parsed is None:  # 会话失效
            self.login()
            parsed = self._request_json(method, path, data)
        if parsed is None:
            raise XuiError(f"{method} {path} 会话重登后仍未拿到 JSON 响应")
        if not parsed.get("success"):
            raise XuiError(f"{method} {path} 失败：{parsed.get('msg')!r}")
        return parsed

    def _request_json(self, method: str, path: str, data: dict | None) -> dict | None:
        with self._new_client() as c:
            resp = c.request(
                method, f"{self.base_url}{path}", cookies=self._cookies, data=data
            )
        if resp.is_redirect:
            return None
        try:
            return resp.json()
        except ValueError:
            return None  # 多半是被打回登录页 HTML

    def list_inbounds(self) -> list[dict]:
        data = self._call("GET", "/panel/api/inbounds/list")
        obj = data.get("obj")
        return list(obj) if isinstance(obj, list) else []

    def create_socks_inbound(
        self, user: str, password: str, port: int, expiry_ms: int, remark: str
    ) -> int:
        settings = json.dumps(
            {
                "auth": "password",
                "accounts": [{"user": user, "pass": password}],
                "udp": True,
                "ip": "127.0.0.1",
            }
        )
        form = {
            "remark": remark,
            "enable": "true",
            "expiryTime": str(expiry_ms),
            "listen": "",
            "port": str(port),
            "protocol": "mixed",
            "settings": settings,
            "streamSettings": "{}",
            "sniffing": _SNIFFING,
            "allocate": "{}",
        }
        data = self._call("POST", "/panel/api/inbounds/add", data=form)
        obj = data.get("obj") or {}
        iid = obj.get("id") if isinstance(obj, dict) else None
        if iid is None:
            raise XuiError(f"建 inbound 响应缺 id：{data!r}")
        return int(iid)

    def delete_inbound(self, inbound_id: int) -> None:
        try:
            self._call("POST", f"/panel/api/inbounds/del/{inbound_id}")
        except XuiError as exc:
            # 已不存在等价于目标达成；其余失败只警告——inbound 带 expiryTime 兜底。
            log(f"删除 inbound {inbound_id} 失败（{exc}），忽略。")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_xui.py -q`
Expected: PASS（8 passed）

- [ ] **Step 5: Commit**

```bash
git add claude_register/xui.py tests/test_xui.py
git commit -m "feat(xui): 3x-ui 单节点 API 客户端(login/list/add mixed/del)"
```

---

### Task 2: ProxyPool — 跨节点开号器

**Files:**
- Create: `claude_register/proxy_pool.py`
- Test: `tests/test_proxy_pool.py`

**Interfaces:**
- Consumes: `claude_register.xui.XuiClient` / `XuiError`（Task 1）。
- Produces:
  - `@dataclass(frozen=True) class XuiNode(name: str, base_url: str, username: str, password: str, proxy_host: str = "")`
  - `@dataclass(frozen=True) class ProvisionedProxy(url: str, node_name: str, inbound_id: int, expiry_ms: int)`
  - `class ProxyPoolError(Exception)`
  - `class ProxyPool(nodes: list[XuiNode], *, expiry_days: int, port_range: tuple[int, int], client_factory=XuiClient, now_ms: Callable[[], int] | None = None, rng: random.Random | None = None)`
  - `ProxyPool.provision(email: str) -> ProvisionedProxy`
  - `ProxyPool.revoke(proxy: ProvisionedProxy) -> None`
  - `ProxyPool.cleanup_expired() -> dict[str, int]`（节点名 → 删除数）

- [ ] **Step 1: Write the failing test**

```python
# tests/test_proxy_pool.py
from __future__ import annotations

import random

import pytest

from claude_register.proxy_pool import (
    ProvisionedProxy,
    ProxyPool,
    ProxyPoolError,
    XuiNode,
)
from claude_register.xui import XuiError


class FakeXui:
    """记录调用的假客户端；行为按 name 从 registry 取。"""

    registry: dict = {}

    def __init__(self, base_url, username, password, *, proxy_host="", timeout=30.0):
        self.base_url = base_url
        self.proxy_host = proxy_host or "host-" + username
        self.spec = FakeXui.registry[username]  # 用 username 当 key 区分节点
        self.created: list = []

    def list_inbounds(self):
        if self.spec.get("down"):
            raise XuiError("node down")
        return self.spec.get("inbounds", [])

    def create_socks_inbound(self, user, password, port, expiry_ms, remark):
        if port in self.spec.get("taken", set()):
            raise XuiError("port already in use")
        self.created.append((user, password, port, expiry_ms, remark))
        self.spec.setdefault("created_ids", []).append(port)
        return port  # 用 port 当 inbound id 方便断言

    def delete_inbound(self, inbound_id):
        self.spec.setdefault("deleted", []).append(inbound_id)


def _node(name, username):
    return XuiNode(name=name, base_url=f"https://{name}.test:2053", username=username,
                   password="pw", proxy_host=f"{name}.example.com")


def _pool(nodes, specs, *, seed=0, now_ms=1_000):
    FakeXui.registry = specs
    return ProxyPool(
        nodes,
        expiry_days=30,
        port_range=(45000, 45010),
        client_factory=FakeXui,
        now_ms=lambda: now_ms,
        rng=random.Random(seed),
    )


def test_provision_builds_socks_url_with_expiry_and_remark():
    specs = {"u1": {"inbounds": []}}
    pool = _pool([_node("n1", "u1")], specs, now_ms=1_000)
    got = pool.provision("x@mail.test")
    assert isinstance(got, ProvisionedProxy)
    assert got.node_name == "n1"
    assert got.url.startswith("socks5://")
    assert "@n1.example.com:" in got.url
    # 30 天 = 30*86400*1000 毫秒后到期
    assert got.expiry_ms == 1_000 + 30 * 86_400 * 1_000
    user, pw, port, expiry, remark = specs["u1"]["created_ids"] and \
        FakeXui.registry  # noqa — 见下断言
    assert remark_of(specs, "u1") == "reg:x@mail.test"


def remark_of(specs, u):
    # 便捷取回最后一次 create 的 remark
    return specs[u]["last_remark"]


def test_provision_avoids_used_ports():
    specs = {"u1": {"inbounds": [{"port": 45000}, {"port": 45001}], "last_remark": ""}}
    pool = _pool([_node("n1", "u1")], specs)
    got = pool.provision("x@mail.test")
    assert got.inbound_id not in (45000, 45001)


def test_provision_retries_on_port_taken_then_succeeds():
    specs = {"u1": {"inbounds": [], "taken": {45000}, "last_remark": ""}}
    pool = _pool([_node("n1", "u1")], specs)
    got = pool.provision("x@mail.test")
    assert got.inbound_id != 45000


def test_provision_falls_over_to_next_node_when_first_down():
    specs = {"bad": {"down": True}, "good": {"inbounds": [], "last_remark": ""}}
    pool = _pool([_node("n1", "bad"), _node("n2", "good")], specs, seed=1)
    got = pool.provision("x@mail.test")
    assert got.node_name == "n2"


def test_provision_raises_when_all_nodes_fail():
    specs = {"bad": {"down": True}}
    pool = _pool([_node("n1", "bad")], specs)
    with pytest.raises(ProxyPoolError):
        pool.provision("x@mail.test")


def test_revoke_deletes_on_matching_node():
    specs = {"u1": {"inbounds": [], "last_remark": ""}}
    pool = _pool([_node("n1", "u1")], specs)
    pool.revoke(ProvisionedProxy(url="socks5://a:b@h:1", node_name="n1",
                                 inbound_id=99, expiry_ms=0))
    assert specs["u1"]["deleted"] == [99]


def test_cleanup_expired_deletes_only_expired_reg_inbounds():
    specs = {"u1": {"inbounds": [
        {"id": 1, "remark": "reg:a@m", "expiryTime": 500},    # 已过期
        {"id": 2, "remark": "reg:b@m", "expiryTime": 5000},   # 未过期
        {"id": 3, "remark": "reg:c@m", "expiryTime": 0},      # 永久，跳过
        {"id": 4, "remark": "manual", "expiryTime": 100},     # 非 reg:，跳过
    ], "last_remark": ""}}
    pool = _pool([_node("n1", "u1")], specs, now_ms=1_000)
    res = pool.cleanup_expired()
    assert res == {"n1": 1}
    assert specs["u1"]["deleted"] == [1]
```

（注：`test_provision_builds_socks_url_with_expiry_and_remark` 里 remark 取值依赖实现把最后一次 create 的 remark 记进 `specs["uX"]["last_remark"]`；为此在 FakeXui.create_socks_inbound 末尾加一行 `self.spec["last_remark"] = remark`。实现测试时把该行补进 FakeXui。）

- [ ] **Step 2: 修正 FakeXui 记录 remark，然后运行测试确认失败**

在 `FakeXui.create_socks_inbound` 的 `return port` 之前加：

```python
        self.spec["last_remark"] = remark
```

Run: `uv run pytest tests/test_proxy_pool.py -q`
Expected: FAIL（`ModuleNotFoundError: claude_register.proxy_pool`）

- [ ] **Step 3: Write minimal implementation**

```python
# claude_register/proxy_pool.py
"""跨多台 3x-ui 节点的按账号开号器。

provision：随机挑一台节点 → 在该节点选空闲端口 → 建 mixed(socks5) inbound
（inbound 级 expiryTime = 现在 + expiry_days）→ 返回 socks5 URL + 撤销句柄。
节点不可达/登录失败自动切下一台。revoke/cleanup_expired 供失败回收与面板清理。
"""

from __future__ import annotations

import secrets
import string
from collections.abc import Callable
from dataclasses import dataclass

from claude_register.console import log
from claude_register.xui import XuiClient, XuiError

_DAY_MS = 86_400 * 1_000
_PORT_TRIES = 8  # 同一节点内换端口重试次数（撞端口用）
_CRED_ALPHABET = string.ascii_letters + string.digits


@dataclass(frozen=True)
class XuiNode:
    name: str
    base_url: str
    username: str
    password: str
    proxy_host: str = ""


@dataclass(frozen=True)
class ProvisionedProxy:
    url: str
    node_name: str
    inbound_id: int
    expiry_ms: int


class ProxyPoolError(Exception):
    """全部节点都开号失败时抛出。"""


def _rand_cred(rng, n: int = 12) -> str:
    return "".join(rng.choice(_CRED_ALPHABET) for _ in range(n))


class ProxyPool:
    def __init__(
        self,
        nodes,
        *,
        expiry_days: int,
        port_range,
        client_factory=XuiClient,
        now_ms: Callable[[], int] | None = None,
        rng=None,
    ) -> None:
        self.nodes = list(nodes)
        self.expiry_days = int(expiry_days)
        self.port_lo, self.port_hi = int(port_range[0]), int(port_range[1])
        self._factory = client_factory
        self._now_ms = now_ms or _default_now_ms
        import random as _random

        self._rng = rng or _random.Random()

    def _client(self, node: XuiNode) -> XuiClient:
        return self._factory(
            node.base_url, node.username, node.password, proxy_host=node.proxy_host
        )

    def provision(self, email: str) -> ProvisionedProxy:
        if not self.nodes:
            raise ProxyPoolError("未配置任何 3x-ui 节点")
        expiry_ms = self._now_ms() + self.expiry_days * _DAY_MS
        remark = f"reg:{email}"
        order = list(self.nodes)
        self._rng.shuffle(order)
        last_err: Exception | None = None
        for node in order:
            try:
                client = self._client(node)
                used = {ib.get("port") for ib in client.list_inbounds()}
                proxy = self._provision_on(client, node, used, expiry_ms, remark)
                if proxy is not None:
                    return proxy
            except XuiError as exc:  # 登录失败/列表失败 → 换下一台
                last_err = exc
                log(f"节点 {node.name} 开号失败（{exc}），尝试下一台。")
        raise ProxyPoolError(f"所有节点均开号失败：{last_err}")

    def _provision_on(self, client, node, used, expiry_ms, remark):
        for _ in range(_PORT_TRIES):
            port = self._rng.randint(self.port_lo, self.port_hi)
            if port in used:
                continue
            user, password = _rand_cred(self._rng), _rand_cred(self._rng)
            try:
                iid = client.create_socks_inbound(
                    user, password, port, expiry_ms, remark
                )
            except XuiError as exc:
                used.add(port)  # 撞端口或该端口不可用，换一个再试
                log(f"节点 {node.name} 端口 {port} 建号失败（{exc}），换端口。")
                continue
            url = f"socks5://{user}:{password}@{client.proxy_host}:{port}"
            return ProvisionedProxy(
                url=url, node_name=node.name, inbound_id=iid, expiry_ms=expiry_ms
            )
        return None  # 端口重试用尽，让 provision 换下一台节点

    def revoke(self, proxy: ProvisionedProxy) -> None:
        node = next((n for n in self.nodes if n.name == proxy.node_name), None)
        if node is None:
            log(f"撤销代理找不到节点 {proxy.node_name}，忽略。")
            return
        try:
            self._client(node).delete_inbound(proxy.inbound_id)
        except XuiError as exc:
            log(f"撤销代理 inbound {proxy.inbound_id} 失败（{exc}），忽略。")

    def cleanup_expired(self) -> dict[str, int]:
        now = self._now_ms()
        result: dict[str, int] = {}
        for node in self.nodes:
            deleted = 0
            try:
                client = self._client(node)
                for ib in client.list_inbounds():
                    remark = str(ib.get("remark") or "")
                    exp = int(ib.get("expiryTime") or 0)
                    if remark.startswith("reg:") and 0 < exp < now:
                        client.delete_inbound(int(ib["id"]))
                        deleted += 1
            except XuiError as exc:
                log(f"清理节点 {node.name} 过期 inbound 失败（{exc}），跳过。")
            result[node.name] = deleted
        return result


def _default_now_ms() -> int:
    import time

    return int(time.time() * 1000)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_proxy_pool.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add claude_register/proxy_pool.py tests/test_proxy_pool.py
git commit -m "feat(proxy-pool): 跨 3x-ui 节点随机开专属 socks5(建/撤/清理过期)"
```

---

### Task 3: config_store 扩展 xui 配置段

**Files:**
- Modify: `server/config_store.py`
- Test: `tests/test_config_store.py`（追加用例）

**Interfaces:**
- Consumes: 无（纯数据）。
- Produces（`Config` 新增字段，供 Task 4/5 读取）：
  - `Config.xui_enabled: bool = False`
  - `Config.xui_expiry_days: int = 30`
  - `Config.xui_port_min: int = 40000`
  - `Config.xui_port_max: int = 60000`
  - `Config.xui_nodes: tuple = ()`（元素为 dict：`{"name","base_url","username","password","proxy_host"}`）
  - `to_redacted_dict` 返回值新增上述键；`xui_nodes` 中每个 node 的 `password` 脱敏为 `REDACTED`。
  - `save_config` 接受 `xui_enabled/xui_expiry_days/xui_port_min/xui_port_max/xui_nodes`；node 密码留空或为 `REDACTED` 时按 `name` 匹配沿用旧密码。

- [ ] **Step 1: Write the failing test（追加到 tests/test_config_store.py 末尾）**

```python
def test_xui_defaults(tmp_path):
    cfg = load_config(tmp_path / "nope.yaml")
    assert cfg.xui_enabled is False
    assert cfg.xui_expiry_days == 30
    assert cfg.xui_port_min == 40000
    assert cfg.xui_port_max == 60000
    assert cfg.xui_nodes == ()


def test_xui_nodes_roundtrip(tmp_path):
    p = tmp_path / "config.yaml"
    node = {"name": "usa-4", "base_url": "https://usa-4.test:2053/xyz",
            "username": "u", "password": "secret", "proxy_host": "usa-4.example.com"}
    save_config(p, {"xui_enabled": True, "xui_expiry_days": 15,
                    "xui_port_min": 41000, "xui_port_max": 42000,
                    "xui_nodes": [node]})
    cfg = load_config(p)
    assert cfg.xui_enabled is True
    assert cfg.xui_expiry_days == 15
    assert cfg.xui_port_min == 41000
    assert cfg.xui_port_max == 42000
    assert len(cfg.xui_nodes) == 1
    assert cfg.xui_nodes[0]["name"] == "usa-4"
    assert cfg.xui_nodes[0]["password"] == "secret"
    assert cfg.xui_nodes[0]["proxy_host"] == "usa-4.example.com"


def test_xui_yaml_uses_port_range_list(tmp_path):
    p = tmp_path / "config.yaml"
    save_config(p, {"xui_port_min": 41000, "xui_port_max": 42000})
    raw = p.read_text(encoding="utf-8")
    assert "port_range" in raw  # 落盘为 [min, max] 而非两个散字段


def test_xui_node_password_redacted(tmp_path):
    p = tmp_path / "config.yaml"
    node = {"name": "usa-4", "base_url": "https://x", "username": "u",
            "password": "secret", "proxy_host": ""}
    save_config(p, {"xui_nodes": [node]})
    d = to_redacted_dict(load_config(p))
    assert d["xui_nodes"][0]["password"] == REDACTED
    assert d["xui_nodes"][0]["name"] == "usa-4"


def test_xui_node_blank_password_keeps_existing(tmp_path):
    p = tmp_path / "config.yaml"
    save_config(p, {"xui_nodes": [{"name": "usa-4", "base_url": "https://x",
                                   "username": "u", "password": "secret",
                                   "proxy_host": ""}]})
    # 二次保存：同名节点密码传 REDACTED（面板脱敏回传）→ 应沿用旧密码
    save_config(p, {"xui_nodes": [{"name": "usa-4", "base_url": "https://x2",
                                   "username": "u2", "password": REDACTED,
                                   "proxy_host": ""}]})
    cfg = load_config(p)
    assert cfg.xui_nodes[0]["password"] == "secret"
    assert cfg.xui_nodes[0]["base_url"] == "https://x2"  # 其余字段照常更新
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config_store.py -q`
Expected: FAIL（`AttributeError: ... has no attribute 'xui_enabled'`）

- [ ] **Step 3: Write minimal implementation**

在 `server/config_store.py` 的 `Config` dataclass 追加字段（在 `register_proxy` 之后）：

```python
    xui_enabled: bool = False
    xui_expiry_days: int = 30
    xui_port_min: int = 40000
    xui_port_max: int = 60000
    xui_nodes: tuple = ()
```

顶部 import 追加 `field`：

```python
from dataclasses import dataclass, field, replace
```

（`xui_nodes: tuple = ()` 是不可变默认，无需 `field`；`field` 此处非必需，若已用则保留。可略过此 import 改动。）

在 `load_config` 的 `reg = raw.get("register", {}) or {}` 之后加载 xui 段，并把新字段传进 `Config(...)`：

```python
    xui = raw.get("xui", {}) or {}
    pr = xui.get("port_range") or [40000, 60000]
    nodes = tuple(_load_node(n) for n in (xui.get("nodes") or []))
```

`Config(...)` 调用末尾追加：

```python
        register_proxy=str(reg.get("proxy", "") or ""),
        xui_enabled=bool(xui.get("enabled", False)),
        xui_expiry_days=int(xui.get("expiry_days", 30)),
        xui_port_min=int(pr[0]),
        xui_port_max=int(pr[1]),
        xui_nodes=nodes,
    )
```

在模块内新增节点规整函数：

```python
_NODE_KEYS = ("name", "base_url", "username", "password", "proxy_host")


def _load_node(raw: dict) -> dict:
    d = raw or {}
    return {k: str(d.get(k, "") or "") for k in _NODE_KEYS}
```

`save_config` 中，在按 `_FIELD_MAP` 写 `out` 之后、`write_text` 之前，单独处理 xui 段。先把 xui 标量并进 cfg（它们在 _FIELD_MAP 里没有映射，所以要显式 replace），再拼 yaml 的 `xui`：

在 `save_config` 顶部 `clean = dict(updates)` 之后，处理 nodes 脱敏沿用：

```python
    # xui 标量：不在 _FIELD_MAP，手动并入
    xui_scalar = {}
    for k in ("xui_enabled", "xui_expiry_days", "xui_port_min", "xui_port_max"):
        if k in clean:
            xui_scalar[k] = clean.pop(k)
    incoming_nodes = clean.pop("xui_nodes", None)
```

在 `cfg = replace(cfg, **{...})`（原有 _FIELD_MAP 那行）之后追加：

```python
    cfg = replace(cfg, **xui_scalar)
    if incoming_nodes is not None:
        old_by_name = {n["name"]: n for n in cfg.xui_nodes}
        merged = []
        for raw_node in incoming_nodes:
            node = _load_node(raw_node)
            if node["password"] in ("", REDACTED):
                node["password"] = old_by_name.get(node["name"], {}).get("password", "")
            merged.append(node)
        cfg = replace(cfg, xui_nodes=tuple(merged))
```

在构造 `out` 时追加 xui 段（在 `for field, (section, key) in _FIELD_MAP.items()` 循环之后）：

```python
    out["xui"] = {
        "enabled": cfg.xui_enabled,
        "expiry_days": cfg.xui_expiry_days,
        "port_range": [cfg.xui_port_min, cfg.xui_port_max],
        "nodes": [dict(n) for n in cfg.xui_nodes],
    }
```

`out` 初始化那行改为包含 xui（可选，`out["xui"]` 已在上面赋值，dict 会自动建键，但为顺序稳定可预置）：

```python
    out: dict = {"panel": {}, "anymail": {}, "register": {}, "xui": {}}
```

`to_redacted_dict` 追加 xui 键与 node 密码脱敏：

```python
def to_redacted_dict(cfg: Config) -> dict:
    d = {f: getattr(cfg, f) for f in _FIELD_MAP}
    for secret in ("panel_password", "anymail_api_key"):
        if d[secret]:
            d[secret] = REDACTED
    d["xui_enabled"] = cfg.xui_enabled
    d["xui_expiry_days"] = cfg.xui_expiry_days
    d["xui_port_min"] = cfg.xui_port_min
    d["xui_port_max"] = cfg.xui_port_max
    d["xui_nodes"] = [
        {**n, "password": REDACTED if n.get("password") else ""} for n in cfg.xui_nodes
    ]
    return d
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config_store.py -q`
Expected: PASS（含原有全部用例）

- [ ] **Step 5: Commit**

```bash
git add server/config_store.py tests/test_config_store.py
git commit -m "feat(config): config.yaml 新增 xui 段(节点列表/有效期/端口范围+脱敏)"
```

---

### Task 4: flow 接入 — 注册时开号、失败撤销、成功导出

**Files:**
- Modify: `claude_register/flow.py`
- Test: `tests/test_flow_xui.py`

**Interfaces:**
- Consumes: `ProxyPool`/`XuiNode`/`ProvisionedProxy`（Task 2）；`Config.xui_*`（Task 3）；现有 `run_browser(..., proxy=...)`、`AccountRecord.extra`。
- Produces:
  - 模块级 `flow._build_proxy_pool(config: Config) -> ProxyPool | None`（`xui_enabled` 且有节点时返回池，否则 None）。
  - `flow.run` 在 `xui_enabled` 时：`prepare_mailbox` 后 provision，proxy 传入 `run_browser`；成功把 `{node,inbound_id,expiry_ms}` 写进导出记录的 `extra["xui"]`；失败/未拿到 sessionKey 时 `pool.revoke(...)`。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_flow_xui.py
"""flow 接入 3x-ui 代理池：开号→失败撤销→成功绑定导出；未启用时退回静态代理。"""

from __future__ import annotations

import pytest

from claude_register import anymail, flow
from claude_register.proxy_pool import ProvisionedProxy
from server.config_store import Config


def _mailbox():
    return anymail.Mailbox(id="m1", email="x@mail.test",
                           expires_at="2026-07-31T00:00:00Z")


def _cfg(**kw):
    base = dict(
        anymail_api_key="ak_parent", anymail_base_url="https://mail.test",
        anymail_domain="mail.test",
        xui_enabled=True, xui_expiry_days=30,
        xui_port_min=45000, xui_port_max=45010,
        xui_nodes=({"name": "n1", "base_url": "https://n1.test:2053",
                    "username": "u", "password": "p", "proxy_host": "n1.example.com"},),
    )
    base.update(kw)
    return Config(**base)


@pytest.fixture
def wired(monkeypatch):
    seen = {"revoked": [], "proxies": [], "saved": []}

    monkeypatch.setattr(flow, "validate_proxy", lambda proxy: None)
    monkeypatch.setattr(
        flow, "prepare_mailbox",
        lambda client, **kw: (_mailbox(), "2026-07-30T00:00:00Z"),
    )
    # 子 key 派生关掉，聚焦代理路径
    monkeypatch.setattr(anymail.AnyMailClient, "create_child_key",
                        lambda self, **kw: None)

    class FakePool:
        def provision(self, email):
            p = ProvisionedProxy(url="socks5://a:b@n1.example.com:45001",
                                 node_name="n1", inbound_id=45001,
                                 expiry_ms=99)
            seen["proxies"].append(p)
            return p

        def revoke(self, proxy):
            seen["revoked"].append(proxy.inbound_id)

    monkeypatch.setattr(flow, "_build_proxy_pool", lambda config: FakePool())

    def fake_run_browser(client, mailbox, since, *, proxy=None, **kw):
        seen["proxy_used"] = proxy
        return seen.get("browser_result")

    monkeypatch.setattr(flow, "run_browser", fake_run_browser)
    return seen


def test_provisions_proxy_and_passes_to_browser(wired):
    wired["browser_result"] = {"sessionKey": "sk-1"}
    flow.run(config=_cfg())
    assert wired["proxy_used"] == "socks5://a:b@n1.example.com:45001"
    assert wired["revoked"] == []  # 成功不撤销


def test_revokes_proxy_when_no_session_key(wired):
    wired["browser_result"] = None  # 未拿到 sessionKey
    flow.run(config=_cfg())
    assert wired["revoked"] == [45001]


def test_revokes_proxy_when_browser_raises(wired, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("browser crash")
    monkeypatch.setattr(flow, "run_browser", boom)
    with pytest.raises(RuntimeError):
        flow.run(config=_cfg())
    assert wired["revoked"] == [45001]


def test_disabled_falls_back_to_static_proxy(wired, monkeypatch):
    monkeypatch.setattr(flow, "_build_proxy_pool", lambda config: None)
    wired["browser_result"] = {"sessionKey": "sk-1"}
    flow.run(config=_cfg(xui_enabled=False, register_proxy="socks5://static:1080"))
    assert wired["proxy_used"] == "socks5://static:1080"
    assert wired["revoked"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_flow_xui.py -q`
Expected: FAIL（`AttributeError: module 'claude_register.flow' has no attribute '_build_proxy_pool'`）

- [ ] **Step 3: Write minimal implementation**

`flow.py` 顶部 import 追加：

```python
from claude_register.proxy_pool import ProvisionedProxy, ProxyPool, XuiNode
```

新增构造函数（放在 `run` 之前）：

```python
def _build_proxy_pool(config: "Config") -> ProxyPool | None:
    """启用 xui 且配了节点时返回代理池，否则 None（退回静态 register_proxy）。"""
    if not getattr(config, "xui_enabled", False) or not config.xui_nodes:
        return None
    nodes = [XuiNode(**n) for n in config.xui_nodes]
    return ProxyPool(
        nodes,
        expiry_days=config.xui_expiry_days,
        port_range=(config.xui_port_min, config.xui_port_max),
    )
```

改 `run`：在现有 `proxy = config.register_proxy or None`（config 分支）保留，但在 `validate_proxy(proxy)` 与 `prepare_mailbox` 之间/之后接入代理池。具体改法——把 `validate_proxy` 与开号逻辑改成：

```python
    pool = _build_proxy_pool(config) if config is not None else None
    if pool is None:
        # 静态代理路径：保持原校验（非法代理尽早失败）
        validate_proxy(proxy)

    mailbox, since = prepare_mailbox(
        client, email=email, domain=domain, expires_hours=expires_hours
    )
    log(f"本次邮箱：{mailbox.email} (id={mailbox.id or 'new'})")

    provisioned: ProvisionedProxy | None = None
    if pool is not None:
        provisioned = pool.provision(mailbox.email)
        proxy = provisioned.url
        log(f"已在节点 {provisioned.node_name} 开专属 socks5"
            f"（inbound={provisioned.inbound_id}）。")
```

注意：`provisioned = pool.provision(...)` 若抛错，此时邮箱已建、子 key 尚未派生（下面才 create_child_key）——异常向上传播，本次不落账号；已建邮箱按 AnyMail 有效期自动清理（与现状一致，无需额外回收）。为把开号放在子 key 派生之前，本 Task 将 `create_child_key` 相关块保持在 provision 之后不变即可（provision 紧跟 `log(本次邮箱)` 之后、`child = client.create_child_key(...)` 之前）。

在现有 `try: account = run_browser(...) except BaseException:` 块中追加代理撤销（与 `client.delete_key(child.id)` 并列）：

```python
    try:
        account = run_browser(
            client, mailbox, since,
            auto_login=auto_login, code_timeout=code_timeout,
            proxy=proxy, password=password,
            poll_client=poll_client,
            mail_key=child.plaintext if child else "",
        )
    except BaseException:
        if child:
            client.delete_key(child.id)
            log("注册中断，已撤销本次派生的子 key。")
        if pool is not None and provisioned is not None:
            pool.revoke(provisioned)
            log("注册中断，已撤销本次开的专属代理。")
        raise

    if child and not (account and account.get("sessionKey")):
        client.delete_key(child.id)
        log("注册未成功，已撤销本次派生的子 key。")
    if pool is not None and provisioned is not None and not (
        account and account.get("sessionKey")
    ):
        pool.revoke(provisioned)
        log("注册未成功，已撤销本次开的专属代理。")
```

成功导出携带 xui 信息：`run_browser` 内 `_capture` 已把 `proxy` 写进 `AccountRecord.proxy`。为把 `{node,inbound_id,expiry_ms}` 记进 `extra["xui"]`，给 `run_browser` 加一个可选参数 `proxy_meta: dict | None = None`，在 `_capture` 构造 `AccountRecord` 时若有则 `extra={"xui": proxy_meta}`。改动：

`run_browser` 签名加 `proxy_meta: dict | None = None`；`_capture` 里：

```python
        record = AccountRecord(
            email=mailbox.email,
            password=password or "",
            sessionKey=session_key,
            proxy=proxy or "",
            display_name=display_name,
            mailbox_id=str(mailbox.id or ""),
            mail_key=mail_key,
            mail_base_url=client.base_url if mail_key else "",
            extra={"xui": proxy_meta} if proxy_meta else {},
        )
```

`run` 里调用 `run_browser` 时传：

```python
            proxy_meta=(
                {
                    "node": provisioned.node_name,
                    "inbound_id": provisioned.inbound_id,
                    "expiry_ms": provisioned.expiry_ms,
                }
                if provisioned
                else None
            ),
```

（`test_flow_xui.py` 的 fake_run_browser 用 `**kw` 吞掉 `proxy_meta`，无需改测试。）

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_flow_xui.py tests/test_flow_child_key.py -q`
Expected: PASS（新用例 + 原子 key 用例都过；确认没破坏原回收逻辑）

- [ ] **Step 5: Commit**

```bash
git add claude_register/flow.py tests/test_flow_xui.py
git commit -m "feat(flow): 注册按账号从 3x-ui 池开专属 socks5，失败撤销、成功随账号导出"
```

---

### Task 5: 后端接口 — 测试连接 + 清理过期 inbound

**Files:**
- Modify: `server/app.py`
- Test: `tests/test_app.py`（追加用例）

**Interfaces:**
- Consumes: `XuiClient`（Task 1）、`ProxyPool`/`XuiNode`（Task 2）、`state.config()`（现有）。
- Produces（两个鉴权接口）：
  - `POST /api/xui/test`，body `{base_url, username, password}`；`password` 为空/`REDACTED` 时按 `base_url` 匹配已存节点取真实密码。返回 `{"ok": true, "inbound_count": N}` 或 400 `{detail}`。
  - `POST /api/xui/cleanup`，对配置里所有节点跑 `cleanup_expired`，返回 `{"results": {node_name: count}, "total": N}`。

- [ ] **Step 1: Write the failing test（追加到 tests/test_app.py）**

```python
def _authed(tmp_path, extra=None):
    """建库+登录，返回已带 cookie 的 TestClient（复用 test_app 的 _client）。"""
    from server.config_store import save_config

    save_config(tmp_path / "config.yaml", {"panel_password": "pw", **(extra or {})})
    c = _client(tmp_path)
    c.post("/api/login", json={"password": "pw"})
    return c


def test_xui_test_endpoint_ok(tmp_path, monkeypatch):
    import server.app as appmod

    class FakeXui:
        def __init__(self, base_url, username, password, **kw):
            self.base_url = base_url
        def list_inbounds(self):
            return [{"id": 1}, {"id": 2}]

    monkeypatch.setattr(appmod, "XuiClient", FakeXui)
    c = _authed(tmp_path)
    r = c.post("/api/xui/test", json={
        "base_url": "https://n.test:2053", "username": "u", "password": "p"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "inbound_count": 2}


def test_xui_test_endpoint_reports_failure(tmp_path, monkeypatch):
    import server.app as appmod
    from claude_register.xui import XuiError

    class FakeXui:
        def __init__(self, *a, **k): ...
        def list_inbounds(self):
            raise XuiError("bad creds")

    monkeypatch.setattr(appmod, "XuiClient", FakeXui)
    c = _authed(tmp_path)
    r = c.post("/api/xui/test", json={
        "base_url": "https://n.test:2053", "username": "u", "password": "x"})
    assert r.status_code == 400
    assert "bad creds" in r.json()["detail"]


def test_xui_cleanup_endpoint(tmp_path, monkeypatch):
    import server.app as appmod

    class FakePool:
        def __init__(self, *a, **k): ...
        def cleanup_expired(self):
            return {"n1": 3, "n2": 0}

    monkeypatch.setattr(appmod, "ProxyPool", FakePool)
    c = _authed(tmp_path, extra={
        "xui_enabled": True,
        "xui_nodes": [{"name": "n1", "base_url": "https://n1", "username": "u",
                       "password": "p", "proxy_host": ""}],
    })
    r = c.post("/api/xui/cleanup")
    assert r.status_code == 200
    assert r.json() == {"results": {"n1": 3, "n2": 0}, "total": 3}
```

（`_client` 是 `tests/test_app.py` 现有的 TestClient 构造辅助；`_authed` 复用它并完成登录，与现有测试同风格。）

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_app.py -q -k xui`
Expected: FAIL（404，路由不存在）

- [ ] **Step 3: Write minimal implementation**

`server/app.py` 顶部 import 追加：

```python
from claude_register.xui import XuiClient, XuiError
from claude_register.proxy_pool import ProxyPool, XuiNode
```

在其它路由旁新增（放在 `put_config` 之后）：

```python
    @app.post("/api/xui/test")
    async def xui_test(request: Request, _=Depends(require_auth)):
        body = await request.json()
        base_url = str(body.get("base_url", "") or "")
        username = str(body.get("username", "") or "")
        password = str(body.get("password", "") or "")
        if password in ("", "••••"):
            # 脱敏回传：按 base_url 找已存节点取真实密码
            for n in state.config().xui_nodes:
                if n.get("base_url") == base_url:
                    password = n.get("password", "")
                    break
        try:
            count = len(XuiClient(base_url, username, password).list_inbounds())
        except (XuiError, Exception) as exc:  # noqa: BLE001 — 面板测试连接需回报任何失败
            raise HTTPException(status_code=400, detail=f"连接失败：{exc}")
        return {"ok": True, "inbound_count": count}

    @app.post("/api/xui/cleanup")
    def xui_cleanup(_=Depends(require_auth)):
        cfg = state.config()
        nodes = [XuiNode(**n) for n in cfg.xui_nodes]
        if not nodes:
            return {"results": {}, "total": 0}
        pool = ProxyPool(
            nodes,
            expiry_days=cfg.xui_expiry_days,
            port_range=(cfg.xui_port_min, cfg.xui_port_max),
        )
        results = pool.cleanup_expired()
        return {"results": results, "total": sum(results.values())}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_app.py -q`
Expected: PASS（含原有全部用例）

- [ ] **Step 5: Commit**

```bash
git add server/app.py tests/test_app.py
git commit -m "feat(api): xui 测试连接与清理过期 inbound 接口"
```

---

### Task 6: 面板 UI — 代理池设置 + 节点增删改 + 测试/清理按钮

**Files:**
- Modify: `web/src/api.js`
- Modify: `web/src/pages/Settings.jsx`
- Modify: `web/src/style.css`（表格/按钮样式，可选，沿用现有类名优先）

**Interfaces:**
- Consumes: `GET/PUT /api/config`（含 `xui_*`、`xui_nodes`）、`POST /api/xui/test`、`POST /api/xui/cleanup`（Task 5）。
- Produces: `api.xuiTest(node)`、`api.xuiCleanup()`；Settings 页「代理池」区块。

- [ ] **Step 1: 在 api.js 追加两个方法**

在 `export const api = { ... }` 内、`rerun` 之后追加：

```javascript
  xuiTest: (node) =>
    fetch("/api/xui/test", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(node),
    }).then(j),

  xuiCleanup: () =>
    fetch("/api/xui/cleanup", { method: "POST" }).then(j),
```

- [ ] **Step 2: 在 Settings.jsx 增加代理池区块**

在 `SECRET_FIELDS` 之后加 xui 标量字段定义：

```javascript
const XUI_FIELDS = [
  { key: "xui_enabled", label: "启用 3x-ui 代理池", type: "checkbox" },
  { key: "xui_expiry_days", label: "代理有效期（天）", type: "number" },
  { key: "xui_port_min", label: "端口范围下限", type: "number" },
  { key: "xui_port_max", label: "端口范围上限", type: "number" },
];

const EMPTY_NODE = { name: "", base_url: "", username: "", password: "", proxy_host: "" };
```

在 `save` 里，`payload` 组装后确保 `xui_nodes` 一并提交（form 已含则无需特殊处理；节点密码为 `••••` 时后端按名沿用，前端可原样提交）。

在 `<form>` 内、现有 FIELD_DEFS 渲染之后，追加代理池区块（节点表格 + 测试/清理）：

```jsx
        <fieldset className="settings-group">
          <legend>3x-ui 代理池</legend>
          {XUI_FIELDS.map((f) => (
            <div className="form-field" key={f.key}>
              <label className="field-label" htmlFor={f.key}>{f.label}</label>
              {f.type === "checkbox" ? (
                <input id={f.key} type="checkbox"
                  checked={!!form[f.key]}
                  onChange={(e) => setField(f.key, e.target.checked)} />
              ) : (
                <input id={f.key} className="input" type="number"
                  value={form[f.key] ?? ""}
                  onChange={(e) => setField(f.key, Number(e.target.value))} />
              )}
            </div>
          ))}

          <div className="nodes-table">
            {(form.xui_nodes || []).map((node, i) => (
              <div className="node-row" key={i}>
                {["name", "base_url", "username", "password", "proxy_host"].map((k) => (
                  <input key={k} className="input" placeholder={k}
                    type={k === "password" ? "password" : "text"}
                    value={node[k] ?? ""}
                    onChange={(e) => {
                      const nodes = [...form.xui_nodes];
                      nodes[i] = { ...nodes[i], [k]: e.target.value };
                      setField("xui_nodes", nodes);
                    }} />
                ))}
                <button type="button" className="btn"
                  onClick={() => testNode(node)}>测试</button>
                <button type="button" className="btn btn-danger"
                  onClick={() => {
                    setField("xui_nodes",
                      form.xui_nodes.filter((_, j) => j !== i));
                  }}>删除</button>
              </div>
            ))}
            <button type="button" className="btn"
              onClick={() => setField("xui_nodes",
                [...(form.xui_nodes || []), { ...EMPTY_NODE }])}>
              + 添加节点
            </button>
            <button type="button" className="btn"
              onClick={cleanupExpired}>清理过期 inbound</button>
          </div>
        </fieldset>
```

在组件内加两个处理函数（放在 `save` 附近）：

```javascript
  async function testNode(node) {
    setMessage(""); setError("");
    try {
      const r = await api.xuiTest(node);
      setMessage(`节点连接成功，现有 ${r.inbound_count} 个 inbound`);
    } catch (e) {
      setError(`节点连接失败：${e.body?.detail || e.message}`);
    }
  }

  async function cleanupExpired() {
    setMessage(""); setError("");
    try {
      const r = await api.xuiCleanup();
      setMessage(`已清理过期 inbound：共 ${r.total} 个`);
    } catch {
      setError("清理失败，请重试");
    }
  }
```

- [ ] **Step 3: 构建前端验证无语法/类型错误**

Run: `cd web && bun install && bun run build`
Expected: 构建成功，产出 `web/dist`（无报错）

- [ ] **Step 4: 冒烟检查后端整体测试仍绿**

Run: `uv run pytest -q`
Expected: 全绿

- [ ] **Step 5: Commit**

```bash
git add web/src/api.js web/src/pages/Settings.jsx web/src/style.css
git commit -m "feat(panel): 代理池设置与 3x-ui 节点增删改/测试连接/清理过期"
```

---

## 自查记录（Self-Review）

- **Spec 覆盖**：
  - 新模块 xui.py（Task 1）、proxy_pool.py（Task 2）✓
  - config.yaml `xui` 段 + nodes 读写脱敏（Task 3）✓
  - flow 接入：开号点在 prepare_mailbox 后、失败对称撤销、成功写 extra 导出、禁用退回静态代理（Task 4）✓
  - 面板节点 CRUD + 测试连接 + 清理过期按钮（Task 5 后端 + Task 6 前端）✓
  - 错误处理：节点失败切换、端口重试、全失败抛错（Task 2 测试覆盖）✓
- **占位扫描**：无 TBD/TODO；每步含实际代码 ✓
- **类型一致性**：`XuiClient.create_socks_inbound(user,password,port,expiry_ms,remark)->int`、`ProvisionedProxy(url,node_name,inbound_id,expiry_ms)`、`ProxyPool.provision/revoke/cleanup_expired`、`Config.xui_enabled/xui_expiry_days/xui_port_min/xui_port_max/xui_nodes`、`flow._build_proxy_pool`、`run_browser(...,proxy_meta=...)` 在各 Task 间签名一致 ✓
- **手动/环境依赖**：Task 6 无 JS 单测框架，以 `bun run build` 作为验证；真实面板端到端已在设计阶段手工验证。
