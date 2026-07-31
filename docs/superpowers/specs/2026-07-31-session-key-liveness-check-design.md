# Session Key 存活检测 设计

日期:2026-07-31

## 背景与目标

面板里每个账号存了 claude.ai 的 `sessionKey`(注册时抓取,接管时注入 cookie)。目前无法知道某个 session 是否仍然有效——只能手动"接管"进去看。

本功能给每个账号加一个"检测"按钮,拿它的 session key 真实请求 claude.ai 鉴权接口,判定该 session 当前是否存活,并把结果落库、在面板展示。

**范围**:仅手动逐个检测(每行一个按钮)。不做批量、不做定时后台检测。

## 三态定义

| 状态 | 含义 | 判定 |
|------|------|------|
| **有效** `alive` | session 仍可用 | HTTP 200 且响应体为含组织信息的 JSON |
| **失效** `dead` | session 已被清除/封禁 | HTTP 401/403 且响应体为 claude 的 JSON 错误 |
| **检测失败** `error` | 无法判定 | 连接/代理错误、超时;或 403 但响应体是 HTML / 带 `cf-mitigated` 头(Cloudflare 盾);或该账号无 session_key |

关键点:Cloudflare 盾的 403 与真实鉴权失败的 403 必须区分开——前者响应体是 HTML(或含 `cf-mitigated` 响应头),归入"检测失败"而非"失效",避免把有效账号误判成失效。

## 组件

### 1. 检测核心 `claude_register/session_check.py`(新增)

纯函数,不碰数据库、不碰 FastAPI,便于单测:

```python
CheckResult = tuple[str, str]  # (status, detail);status ∈ {"alive","dead","error"}

def check_session(session_key: str, proxy: str | None = None,
                  *, timeout: float = 15.0) -> CheckResult:
    ...
```

行为:
- `session_key` 为空 → 直接返回 `("error", "无 sessionKey")`,不发请求。
- 用 httpx 同步 `Client` GET `https://claude.ai/api/organizations`:
  - cookie:`{"sessionKey": session_key}`
  - headers:一组常规浏览器头(User-Agent、Accept、Accept-Language),降低被盾概率。
  - 代理:`proxy` 非空则传给 httpx(`proxy=<url>`),否则直连。代理 URL 复用现有 `normalize_proxy_url` 归一化;解析失败按"检测失败"处理并在 detail 里说明。
- 响应判定按上表。JSON/HTML 判定:优先看 `Content-Type` 与响应体是否能 `json.loads`,HTML 或解析失败视作盾/异常。
- 所有 httpx 异常(超时、连接、代理)捕获 → `("error", <简短原因>)`。

**依赖**:httpx 已装(0.28.1)。socks5 代理经 httpx 需要 `socksio`,当前**未装**,而 x-ui 给账号发的正是 socks5 代理。因此 `pyproject.toml` 依赖加 `httpx[socks]`(或显式加 `socksio`)。若运行环境仍缺 socksio,socks5 检测会抛错并被归入"检测失败",detail 提示缺依赖——不静默降级为直连。

### 2. 数据层 `server/db.py`

accounts 表加两列(走现有 `_ACCOUNT_EXTRA_COLS` 迁移机制,旧库自动补列):
- `check_status TEXT` — `alive` / `dead` / `error` / NULL(从未检测)
- `checked_at TEXT` — ISO 时间戳

新增 `update_account_check(conn, email, status, checked_at)`:只更新这两列。`list_accounts` / `get_account` 用 `SELECT *`,新列自然带出。

### 3. 接口 `server/app.py`

```
POST /api/accounts/{email}/check   (require_auth)
```
- 取账号 row;不存在 → 404。
- `checked_at = 当前 UTC ISO`。
- `status, detail = await asyncio.to_thread(check_session, row["session_key"], row.get("proxy") or "")`
  (httpx 同步调用,丢线程池避免阻塞事件循环)。
- `db.update_account_check(...)` 落库。
- 返回 `{"status": status, "detail": detail, "checked_at": checked_at}`。

### 4. 前端 `web/src/pages/Dashboard.jsx` + `api.js`

- `api.js` 加 `checkAccount(email)` → `POST /api/accounts/{email}/check`。
- 账号行在"重跑"旁加**"检测"按钮**,仅当 `a.session_key` 存在时显示。
- 点击:按钮进入"检测中…"禁用态;成功后就地更新该账号的 `check_status` / `checked_at`(用返回值),失败显示错误。
- 状态徽章:在 `<StatusBadge>`(注册状态)旁再加一个存活徽章,复用 `check_status`:
  - `alive` → 🟢 有效
  - `dead` → 🔴 失效
  - `error` → ⚪ 检测失败(hover/title 显示 detail)
  - NULL → 不显示(或"未检测")
  - 后附 `checked_at` 的相对时间(如"3 分钟前")。
- 样式加对应 badge class(绿/红/灰),沿用现有 `style.css` 徽章风格。

## 数据流

```
点"检测" → POST /api/accounts/{email}/check
  → 取 session_key + proxy
  → to_thread(check_session)
       → httpx GET claude.ai/api/organizations (带 cookie，可选 proxy)
       → 判定 alive/dead/error
  → update_account_check 落库
  → 返回 {status, detail, checked_at}
→ 前端就地更新徽章
```

## 错误处理

- 账号不存在 → 404。
- 无 session_key → 不发请求,返回 `error / 无 sessionKey`。
- 代理解析失败 / socks5 缺 socksio / 超时 / 连接失败 → `error`,detail 带原因。
- CF 盾 403 → `error`(不误判为 dead)。
- 检测本身绝不抛 500:所有异常在 `check_session` 内收敛为 `error`。

## 测试

`tests/test_session_check.py`(mock httpx transport / respx 或 monkeypatch):
- 200 + JSON 数组 → `alive`
- 401 + JSON → `dead`
- 403 + JSON(claude 错误)→ `dead`
- 403 + HTML / `cf-mitigated` 头 → `error`
- 超时 / 连接错误 → `error`
- 空 session_key → `error`,且不发请求
- 有代理时代理被正确传入(断言 httpx 收到 proxy 参数)

`tests/test_app.py` 增:
- `POST /api/accounts/{email}/check` 打桩 `check_session`,断言落库两列并返回正确 JSON;账号不存在返回 404。

`tests/test_db.py` 增:`update_account_check` 更新与旧库迁移补列。

## 非目标(YAGNI)

- 不做批量"检测全部"。
- 不做定时/后台自动检测。
- 不用浏览器(Camoufox)检测——HTTP 足够,且轻快。
- 不做失效后的自动重跑/清理。
