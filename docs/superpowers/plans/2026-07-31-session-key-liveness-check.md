# Session Key 存活检测 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给面板每个账号加一个"检测"按钮,拿它的 session key 真实请求 claude.ai,判定 session 有效/失效/检测失败,结果落库并在账号行展示。

**Architecture:** 新增纯函数 `check_session`(httpx GET claude.ai 鉴权接口,三态判定,CF 盾与真实失败区分);accounts 表加两列存结果;FastAPI 加一个 POST 接口把检测丢线程池跑并落库;前端 Accounts 页每行加按钮+存活徽章。

**Tech Stack:** Python / httpx(带 socks5 需 socksio)/ FastAPI / SQLite / React(Vite)。

## Global Constraints

- 检测走账号自己的代理:`proxy` 非空则传给 httpx,否则直连(值取自 accounts.proxy)。
- 三态:`alive` / `dead` / `error`。Cloudflare 盾的 403(HTML 体或带 `cf-mitigated` 头)归 `error`,不得判成 `dead`。
- 检测函数绝不向上抛异常:所有失败收敛为 `("error", <原因>)`。
- 包管理用 bun(前端),Python 依赖写进 `pyproject.toml`。**不在本机/服务器执行打包构建**,最多跑 test。
- socks5 代理经 httpx 需要 `socksio`(当前未装);依赖加 `httpx[socks]`。缺 socksio 时 socks5 检测报错并归 `error`,不静默降级为直连。
- 时间戳用 ISO UTC 格式 `%Y-%m-%dT%H:%M:%SZ`(与 `default_now` 一致);接口内用 `state.now_fn()`。

---

### Task 1: 检测核心 `check_session`

**Files:**
- Create: `claude_register/session_check.py`
- Test: `tests/test_session_check.py`

**Interfaces:**
- Consumes: `claude_register.browser.normalize_proxy_url(url) -> str | None`
- Produces: `check_session(session_key: str, proxy: str | None = None, *, timeout: float = 15.0, client_factory=None) -> tuple[str, str]`
  返回 `(status, detail)`,`status ∈ {"alive","dead","error"}`。`client_factory` 仅供测试注入,默认用 httpx。

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_session_check.py
"""check_session：三态 + CF 盾 + 空 key + 代理传参。"""
from __future__ import annotations

import httpx
import pytest

from claude_register.session_check import check_session

ORG_URL = "https://claude.ai/api/organizations"


def _factory(handler):
    """返回一个 client_factory(proxy)->httpx.Client，用 MockTransport 打桩。
    并把最后一次收到的 proxy 记到 captured 里，供断言。"""
    captured = {}

    def make(proxy=None):
        captured["proxy"] = proxy
        return httpx.Client(transport=httpx.MockTransport(handler))

    return make, captured


def test_alive_200_json():
    make, _ = _factory(lambda req: httpx.Response(200, json=[{"uuid": "org1"}]))
    assert check_session("sk-x", client_factory=make) == ("alive", "有效")


def test_dead_401_json():
    make, _ = _factory(lambda req: httpx.Response(401, json={"error": {"type": "authentication_error"}}))
    status, _ = check_session("sk-x", client_factory=make)
    assert status == "dead"


def test_dead_403_json():
    make, _ = _factory(lambda req: httpx.Response(403, json={"error": {"type": "permission_error"}}))
    status, _ = check_session("sk-x", client_factory=make)
    assert status == "dead"


def test_cf_shield_403_html_is_error():
    def handler(req):
        return httpx.Response(403, headers={"cf-mitigated": "challenge"},
                              text="<!DOCTYPE html><html>Just a moment...</html>")
    make, _ = _factory(handler)
    status, detail = check_session("sk-x", client_factory=make)
    assert status == "error"
    assert "盾" in detail or "cloudflare" in detail.lower()


def test_403_html_without_header_is_error():
    make, _ = _factory(lambda req: httpx.Response(403, text="<html>blocked</html>"))
    status, _ = check_session("sk-x", client_factory=make)
    assert status == "error"


def test_connect_error_is_error():
    def handler(req):
        raise httpx.ConnectError("boom")
    make, _ = _factory(handler)
    status, _ = check_session("sk-x", client_factory=make)
    assert status == "error"


def test_empty_key_no_request():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(200, json=[])

    make, _ = _factory(handler)
    status, detail = check_session("", client_factory=make)
    assert status == "error"
    assert calls["n"] == 0  # 没有发请求


def test_proxy_passed_to_factory():
    make, captured = _factory(lambda req: httpx.Response(200, json=[]))
    check_session("sk-x", proxy="socks5://u:p@1.2.3.4:1080", client_factory=make)
    assert captured["proxy"] == "socks5://u:p@1.2.3.4:1080"


def test_no_proxy_passes_none():
    make, captured = _factory(lambda req: httpx.Response(200, json=[]))
    check_session("sk-x", proxy="", client_factory=make)
    assert captured["proxy"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_session_check.py -v`
Expected: FAIL,`ModuleNotFoundError: No module named 'claude_register.session_check'`

- [ ] **Step 3: Write minimal implementation**

```python
# claude_register/session_check.py
"""claude.ai session key 存活检测：拿 sessionKey cookie 请求鉴权接口，判三态。

纯函数，不碰数据库/FastAPI。所有异常收敛为 ("error", 原因)，绝不上抛。
"""
from __future__ import annotations

import httpx

from claude_register.browser import normalize_proxy_url

ORG_URL = "https://claude.ai/api/organizations"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
}


def _default_client(proxy: str | None) -> httpx.Client:
    kwargs = {"timeout": 15.0, "follow_redirects": True}
    if proxy:
        kwargs["proxy"] = proxy
    return httpx.Client(**kwargs)


def _looks_like_shield(resp: httpx.Response) -> bool:
    """Cloudflare 盾：带 cf-mitigated 头，或响应体不是 JSON（HTML 挑战页）。"""
    if "cf-mitigated" in resp.headers:
        return True
    ctype = resp.headers.get("content-type", "")
    if "html" in ctype.lower():
        return True
    try:
        resp.json()
        return False
    except Exception:  # noqa: BLE001
        return True


def check_session(
    session_key: str,
    proxy: str | None = None,
    *,
    timeout: float = 15.0,
    client_factory=None,
) -> tuple[str, str]:
    if not session_key:
        return ("error", "无 sessionKey")
    try:
        proxy_url = normalize_proxy_url(proxy)
    except Exception as exc:  # noqa: BLE001
        return ("error", f"代理无效：{exc}")

    factory = client_factory or _default_client
    try:
        client = factory(proxy_url)
    except Exception as exc:  # noqa: BLE001
        # socks5 缺 socksio 会在建 client 时报错
        return ("error", f"发起请求失败：{exc}")

    try:
        with client:
            resp = client.get(
                ORG_URL,
                headers=_HEADERS,
                cookies={"sessionKey": session_key},
                timeout=timeout,
            )
    except Exception as exc:  # noqa: BLE001
        return ("error", f"请求失败：{type(exc).__name__}")

    if resp.status_code == 200 and not _looks_like_shield(resp):
        return ("alive", "有效")
    if resp.status_code in (401, 403):
        if _looks_like_shield(resp):
            return ("error", "疑似 Cloudflare 盾拦截")
        return ("dead", f"已失效（HTTP {resp.status_code}）")
    return ("error", f"未知响应（HTTP {resp.status_code}）")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_session_check.py -v`
Expected: PASS(9 passed)

- [ ] **Step 5: Add socks dependency to pyproject.toml**

在 `pyproject.toml` 的 dependencies 里把 `"httpx>=0.28.1",` 改为 `"httpx[socks]>=0.28.1",`(为 socks5 代理引入 socksio)。

- [ ] **Step 6: Commit**

```bash
git add claude_register/session_check.py tests/test_session_check.py pyproject.toml
git commit -m "feat(session-check): claude.ai session key 存活检测核心（三态+CF盾区分）"
```

---

### Task 2: 数据层落库两列

**Files:**
- Modify: `server/db.py`(schema 第 13-18 行、迁移列表第 21-28 行、新增 update 函数)
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: 无(纯数据层)
- Produces: `update_account_check(conn, email: str, status: str, checked_at: str) -> bool`;accounts 行新增字段 `check_status`、`checked_at`。

- [ ] **Step 1: Write the failing tests**

```python
# 追加到 tests/test_db.py
from server import db


def test_update_account_check(tmp_path):
    conn = db.init_db(tmp_path / "t.db")
    db.upsert_account(conn, email="a@x.com", domain="x.com", created_at="t0",
                      expires_at=None, mailbox_id="m", last_run_id=1, status="success")
    assert db.update_account_check(conn, "a@x.com", "alive", "2026-07-31T00:00:00Z") is True
    row = db.get_account(conn, "a@x.com")
    assert row["check_status"] == "alive"
    assert row["checked_at"] == "2026-07-31T00:00:00Z"


def test_update_account_check_missing(tmp_path):
    conn = db.init_db(tmp_path / "t.db")
    assert db.update_account_check(conn, "none@x.com", "dead", "t") is False


def test_migrate_adds_check_columns(tmp_path):
    # 老库无新列，init_db 后应补上
    conn = db.init_db(tmp_path / "t.db")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(accounts)").fetchall()}
    assert "check_status" in cols and "checked_at" in cols
```

先确认 `upsert_account` 的参数名(读 `server/db.py` 第 92-136 行)与上面调用一致;若签名不同,按实际签名调整测试里的 kwargs。

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_db.py -k "check_columns or account_check" -v`
Expected: FAIL(`update_account_check` 不存在 / 新列缺失)

- [ ] **Step 3: Add columns to schema and migration**

在 `server/db.py` 的 `_SCHEMA` accounts 表定义末尾(`mail_key TEXT, mail_base_url TEXT` 那行)追加两列:

```sql
  mail_key TEXT, mail_base_url TEXT,
  check_status TEXT, checked_at TEXT
```

在 `_ACCOUNT_EXTRA_COLS` 元组末尾追加(供旧库迁移补列):

```python
    ("mail_base_url", "TEXT"),
    ("check_status", "TEXT"),
    ("checked_at", "TEXT"),
```

- [ ] **Step 4: Add update function**

在 `update_account_fields` 之后追加:

```python
def update_account_check(conn, email, status, checked_at) -> bool:
    """更新单个账号的存活检测结果，返回是否有行被更新。"""
    cur = conn.execute(
        "UPDATE accounts SET check_status=?, checked_at=? WHERE email=?",
        (status, checked_at, email),
    )
    conn.commit()
    return cur.rowcount > 0
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_db.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add server/db.py tests/test_db.py
git commit -m "feat(db): accounts 表加 check_status/checked_at 两列与 update_account_check"
```

---

### Task 3: 检测接口 `POST /api/accounts/{email}/check`

**Files:**
- Modify: `server/app.py`(在 `rerun` 接口后、`takeover_start` 前加接口;顶部 import)
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `check_session(session_key, proxy) -> (status, detail)`;`db.update_account_check(conn, email, status, checked_at)`;`db.get_account`;`state.now_fn()`。
- Produces: `POST /api/accounts/{email}/check` → `200 {"status","detail","checked_at"}`;账号不存在 → 404。

- [ ] **Step 1: Write the failing test**

```python
# 追加到 tests/test_app.py（import 段加：from unittest import mock）
def test_account_check(tmp_path):
    save_config(tmp_path / "config.yaml", {"panel_password": "pw"})
    c = _client(tmp_path)
    c.post("/api/login", json={"password": "pw"})
    # 先塞一个账号
    from server.app import create_app  # noqa
    # 用底层 db 直接插，拿 state 的 conn 不方便 → 走 rerun 前置：直接调 db
    import server.db as db
    # 通过 TestClient 拿不到 conn，改用 patch check_session + 先 upsert
    # 简化：patch check_session，并确保账号存在（用 upsert via 一次注册桩较重，
    # 这里直接对未知账号断言 404，再对已知账号断言 200）
    r404 = c.post("/api/accounts/nobody@x.com/check")
    assert r404.status_code == 404
```

说明:`_client` 里拿不到底层 conn,直接构造"已存在账号"较绕。**实现前先读 `tests/test_app.py` 里是否已有往 accounts 表塞数据的既有 helper**(如从 run 落账号的路径);有就复用它塞一个带 session_key 的账号,再:

```python
    with mock.patch("server.app.check_session", return_value=("alive", "有效")):
        r = c.post(f"/api/accounts/{email}/check")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "alive"
    assert body["detail"] == "有效"
    assert body["checked_at"]  # 非空
```

若无现成 helper,则给 `_client` 增加返回 app 的能力,或用 `create_app` 返回的 state 直接 `db.upsert_account` —— 参照 `test_app.py` 现有账号相关测试(搜索 `accounts` / `upsert`)的写法保持一致。

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_app.py -k account_check -v`
Expected: FAIL(404 分支:接口不存在返回 405/404 不符;或 200 分支报错)

- [ ] **Step 3: Add import and endpoint**

在 `server/app.py` 顶部 import 区加:

```python
from claude_register.session_check import check_session
```

在 `rerun` 接口(`@app.post("/api/accounts/{email}/rerun")` 那个函数)之后追加:

```python
    @app.post("/api/accounts/{email}/check")
    async def account_check(email: str, _=Depends(require_auth)):
        row = db.get_account(state.conn, email)
        if row is None:
            raise HTTPException(status_code=404, detail="账号不存在")
        checked_at = state.now_fn()
        status, detail = await asyncio.to_thread(
            check_session, row.get("session_key") or "", row.get("proxy") or ""
        )
        db.update_account_check(state.conn, email, status, checked_at)
        return {"status": status, "detail": detail, "checked_at": checked_at}
```

确认文件顶部已 `import asyncio`(takeover 用了 `asyncio.to_thread`,应已存在);若无则加。

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_app.py -k account_check -v`
Expected: PASS

- [ ] **Step 5: Run full backend suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 全绿

- [ ] **Step 6: Commit**

```bash
git add server/app.py tests/test_app.py
git commit -m "feat(api): POST /api/accounts/{email}/check 存活检测接口"
```

---

### Task 4: 前端按钮与存活徽章

**Files:**
- Modify: `web/src/api.js`(加 `checkAccount`)
- Modify: `web/src/pages/Accounts.jsx`(状态、handler、按钮、徽章)
- Modify: `web/src/style.css`(存活徽章配色)

**Interfaces:**
- Consumes: `POST /api/accounts/{email}/check` → `{status, detail, checked_at}`;账号行字段 `check_status`、`checked_at`。
- Produces: 无(叶子层)

- [ ] **Step 1: Add api method**

在 `web/src/api.js` 的 `rerun` 之后加:

```javascript
  checkAccount: (email) =>
    fetch(`/api/accounts/${encodeURIComponent(email)}/check`, {
      method: "POST",
    }).then(j),
```

- [ ] **Step 2: Add state + handler in Accounts.jsx**

在 `Accounts` 组件的 useState 区加:

```javascript
  const [checking, setChecking] = useState("");   // 正在检测的 email
  const [checkError, setCheckError] = useState("");
```

在 `doRerun` 附近加 handler(检测成功后就地更新该账号的两列):

```javascript
  async function doCheck(acctEmail) {
    setCheckError("");
    setChecking(acctEmail);
    try {
      const res = await api.checkAccount(acctEmail);
      setAccounts((list) =>
        list.map((a) =>
          a.email === acctEmail
            ? { ...a, check_status: res.status, checked_at: res.checked_at }
            : a,
        ),
      );
    } catch (e) {
      setCheckError(`「${acctEmail}」检测失败（${e.status || "?"}）`);
    } finally {
      setChecking("");
    }
  }
```

- [ ] **Step 3: Add liveness badge helper**

在 `Accounts.jsx` 文件内(组件外顶部)加一个存活徽章小组件:

```javascript
const LIVE_LABEL = { alive: "有效", dead: "失效", error: "检测失败" };

function relTime(iso) {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const sec = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (sec < 60) return "刚刚";
  if (sec < 3600) return `${Math.floor(sec / 60)} 分钟前`;
  if (sec < 86400) return `${Math.floor(sec / 3600)} 小时前`;
  return `${Math.floor(sec / 86400)} 天前`;
}

function LiveBadge({ status, checkedAt, detail }) {
  if (!status) return null;
  return (
    <span className={`badge live-${status}`} title={detail || ""}>
      {LIVE_LABEL[status] || status}
      {checkedAt ? <span className="live-time"> · {relTime(checkedAt)}</span> : null}
    </span>
  );
}
```

- [ ] **Step 4: Render badge + button**

在 `list-sub`(第 186-195 行那段)里,`StatusBadge` 之后加存活徽章:

```javascript
                      <StatusBadge status={a.status} />
                      <LiveBadge status={a.check_status} checkedAt={a.checked_at} />
```

在 `row-actions`(重跑按钮之后、接管之前)加检测按钮,仅当有 session_key 时显示:

```javascript
                    {a.session_key && (
                      <button
                        className="btn btn-small"
                        onClick={() => doCheck(a.email)}
                        disabled={checking === a.email}
                      >
                        {checking === a.email ? "检测中…" : "检测"}
                      </button>
                    )}
```

并在 `exportError` 那一排错误提示旁加 `{checkError && <div className="error-msg">{checkError}</div>}`。

- [ ] **Step 5: Add badge styles**

在 `web/src/style.css` 的 Status badges 区(第 395 行附近,沿用现有 `.badge` 风格)追加:

```css
.badge.live-alive { color: #15803d; background: #dcfce7; }
.badge.live-dead { color: #b91c1c; background: #fee2e2; }
.badge.live-error { color: #6b7280; background: #f3f4f6; }
.live-time { opacity: 0.7; font-weight: normal; }
```

若项目有暗色主题变量,参照相邻 `.badge-*` 的写法对齐(读第 395-445 行确认配色变量用法)。

- [ ] **Step 6: Manual verify(lint,不打包构建)**

Run: `cd web && bunx eslint src/pages/Accounts.jsx src/api.js`(若仓库配了 eslint;没有则跳过)
Expected: 无报错。**不执行 `bun run build`**(约定:本地/服务器不打包)。

- [ ] **Step 7: Commit**

```bash
git add web/src/api.js web/src/pages/Accounts.jsx web/src/style.css
git commit -m "feat(web): 账号行加存活检测按钮与三态徽章"
```

---

## Self-Review

- **Spec 覆盖**:三态(Task 1)、CF 盾区分(Task 1 `_looks_like_shield` + 测试)、走账号代理(Task 1 proxy 传参 + 测试)、落库两列(Task 2)、接口(Task 3)、前端按钮+徽章+相对时间(Task 4)、socksio 依赖(Task 1 Step 5)、测试三处(Task 1/2/3)—— 全部有对应任务。
- **Placeholder**:无 TBD/TODO;Task 3 测试因拿不到底层 conn 而给了"先读现有 helper"的指引 —— 这是真实的实现分支决策,非占位符,已给出两条可行路径。
- **类型一致**:`check_session(session_key, proxy=None, *, timeout, client_factory)` 在 Task 1 定义、Task 3 以位置参数 `(session_key, proxy)` 调用,一致;`update_account_check(conn, email, status, checked_at)` Task 2 定义、Task 3 调用一致;前端字段 `check_status`/`checked_at` Task 2/3/4 一致。

## 备注:worktree

按仓库约定(treeflow),实现应在独立 git worktree 里进行。执行前用 `superpowers:using-git-worktrees` 建 worktree(分支名如 `session-key-liveness-check`),四个 Task 各自 commit,完成后合并回 main。
