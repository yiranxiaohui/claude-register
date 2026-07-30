# noVNC 免密登录接管 — 设计

日期：2026-07-30
状态：已定稿，待写实现计划

## 背景与目标

`claude-register` 注册成功后，会抓取并落盘 Claude 的 `sessionKey`（claude.ai 的会话 Cookie）。
持有一份有效 `sessionKey` 即可免密恢复登录态，但当前项目只做「抓取保存」，没有「拿它回填并让人接管操作」的能力：

- 注册浏览器跑在容器里的 Xvfb 虚拟屏上（Camoufox `headless="virtual"`），生命周期极短（开→跑流程→关），且**看不到画面**。
- 容器只装了 `xvfb`，没有 `x11vnc`/`websockify`/`novnc`，`docker-compose.yml` 也不暴露 VNC 端口。

本设计新增能力：**在账号列表里挑一个已存 `sessionKey` 的账号 → 后台开一个注入好 Cookie、已登录 claude.ai 的浏览器 → 用户通过面板内的 noVNC 网页实时接管操作**。

### 明确不做（YAGNI）

- 不围观正在跑的注册浏览器（注册屏不推流）。
- 不支持多个接管会话并行（同一时刻只允许一个）。
- 不新增 DB schema（`sessionKey`、`proxy` 已按账号存好）。
- 不对外暴露新端口（一切经面板 8790 反代、全部 localhost 绑定）。

## 架构总览

新增一个**接管会话管理器**，与注册流程完全独立——各自的锁、各自的屏。注册流程照旧使用 Camoufox 自选的 `"virtual"` Xvfb，不受影响，二者天然并行。

```
面板 (8790, 已鉴权)
  ├─ POST /api/takeover/start {email}   启动接管
  ├─ POST /api/takeover/stop            结束接管
  ├─ GET  /api/takeover                 查询状态
  ├─ GET  /vnc/*        (StaticFiles)   noVNC 前端静态资源（鉴权）
  └─ WS   /vnc/websockify               鉴权后桥接到 127.0.0.1:6080

接管会话（后台，单例）：
  Xvfb :100
    → Camoufox(headless=False, DISPLAY=:100, 注入 sessionKey + 账号 proxy)
    → goto https://claude.ai   （已登录态）
    → x11vnc  -display :100 -localhost -rfbport 5900
    → websockify 127.0.0.1:6080 → 127.0.0.1:5900
```

### 为什么要自己管一块固定的 `:100`

现有 `browser_session()` 走 Camoufox 的 `"virtual"` 档，它在内部 `Popen` 一个 Xvfb 并随机挑一个空闲 display——**外部无法定位是哪块屏**，x11vnc 就没法挂。因此接管会话不能复用 `"virtual"`：

- 由管理器自己 `Popen` 一个 `Xvfb :100`；
- Camoufox 以 `headless=False` + 环境变量 `DISPLAY=:100` 启动，从而复用这块已知的屏；
- x11vnc `-display :100` 精确挂到它上面。

固定端口约定（全部只绑 `127.0.0.1`）：display `:100`、VNC `5900`、websockify `6080`。因为是单例接管，固定端口不会冲突。

## 组件

### 1. `server/takeover.py` — TakeoverManager

单会话管理器，与 `server/runner.py` 的注册 Runner 平级、互不共享锁。

职责：

- **`start(account)`**（account 含 email / sessionKey / proxy）：
  1. 起 `Xvfb :100`（子进程）；
  2. 起 Camoufox（`headless=False`、`DISPLAY=:100`、代理/geoip/指纹逻辑复用现有 `browser_session` 的实现，见下）；
  3. `context.add_cookies([...])` 注入 `sessionKey`（domain `.claude.ai`、path `/`、secure、httpOnly）；
  4. `page.goto("https://claude.ai", wait_until="domcontentloaded")`；
  5. 起 `x11vnc -display :100 -localhost -forever -shared -rfbport 5900 -nopw`；
  6. 起 `websockify 127.0.0.1:6080 127.0.0.1:5900`。
- **`stop()`**：反序拆掉 websockify → x11vnc → Camoufox → Xvfb 全部子进程，重置状态。
- **`status()`**：返回 `running`、`email`、`started_at`。
- **空闲/安全超时**：默认 15 分钟（`config.yaml` 可调）自动 `stop()`，防止忘关长期占屏、占着已登录会话。计时以启动时刻为准（安全上限，不做精细的鼠标活动检测——那需要探 x11vnc 客户端状态，YAGNI）。
- **单例互斥**：内部锁保证同一时刻只有一个接管会话。

**代理/指纹逻辑复用**：把 `browser_session()` 里「解析代理 → 需要时起 SocksRelay → 决定 geoip → 组 Camoufox kwargs」这段抽出为一个可被两处调用的辅助（如 `build_camoufox_kwargs(proxy) -> (kwargs, relay)`），注册流程和接管会话都用它，避免复制粘贴。接管会话与注册的唯一差别是 `headless`/`DISPLAY`：接管强制 `headless=False` 且预置 `DISPLAY=:100`。

**可测试性**：把「起 Xvfb / x11vnc / websockify」三个外部子进程抽象成一个可注入的 `ProcessLauncher` 接口（默认实现用 `subprocess.Popen`）。单测注入假实现，不真的拉起 X 服务。Camoufox 沿用现有测试里的 `fake_camoufox` 手法注入。

### 2. noVNC 反代进面板

- **Dockerfile**：`apt-get install` 增加 `x11vnc`、`websockify`、`novnc`（noVNC 提供 `vnc.html` + 静态前端；`websockify` 提供 ws↔tcp 桥）。
- **静态资源**：noVNC 前端用 FastAPI `StaticFiles` 挂在 `/vnc`，该路由走 `require_auth`（未登录 401）。
- **WebSocket 桥接**：新增 FastAPI WebSocket 端点 `/vnc/websockify`，先校验面板 cookie（复用 `auth.verify_token`），通过后把该 WS 与本机 `127.0.0.1:6080`（websockify 裸桥）双向转发。
- **绑定**：x11vnc、websockify 一律 `127.0.0.1`；容器**不新增 EXPOSE / ports**，唯一入口是面板 8790 的鉴权反代。

> 为什么静态与 ws 分开：websockify 自身也能托管 noVNC 静态，但那会绕过面板鉴权。让静态走 `StaticFiles`（鉴权路由）、ws 走鉴权 FastAPI 端点，保证两条路径都经过面板密码校验。

### 3. Web UI

- 账号列表：凡 `sessionKey` 非空的账号，加一个「接管」按钮 → `POST /api/takeover/start {email}` → 成功后打开 noVNC 前端（新标签页 `/vnc/vnc.html?autoconnect=1&path=vnc/websockify`）。
- 顶部或账号区显示当前接管状态（正在接管哪个 email、起始时间）+「结束接管」按钮 → `POST /api/takeover/stop`。
- 无 `sessionKey` 的账号不显示「接管」按钮（或禁用并提示）。

## 数据与配置

- **无 DB schema 变更**：`start` 时按 email 查库取 `session_key` 与 `proxy`。
- **新增配置段** `takeover`：
  - `enabled`（默认 `true`）：关掉后所有 takeover 路由返回 403/404。
  - `idle_timeout_min`（默认 `15`）：接管会话安全超时分钟数。
- 配置沿用现有 `config_store` 读写与脱敏机制。

## 错误处理与并发

| 场景 | 行为 |
|------|------|
| 已有接管在跑时再 start | `409`，提示「已有接管会话，请先结束」 |
| 账号无 `sessionKey` | `400` |
| 账号 email 查不到 | `404` |
| `takeover.enabled=false` | `403` |
| 未登录面板（含 `/vnc/*`、WS） | `401` |
| Xvfb/x11vnc/websockify 任一起不来 | `start` 内部回滚已起的子进程，返回 `500` 并把原因写日志 |
| 服务器关闭 / 进程退出 | 兜底 `stop()` 清理全部子进程，避免僵尸占屏 |

注册流程与接管会话互不阻塞：注册用 Camoufox `"virtual"` 自选屏，接管用固定 `:100`，端口/display 不重叠。

## 安全

接管会把「已登录 Claude 会话的完全控制权」交给连上 noVNC 的人——这是功能本意。防护依赖：

1. **面板密码**：所有 takeover 路由、noVNC 静态、WS 桥全部 `require_auth`。
2. **全 localhost 绑定**：x11vnc `-localhost`、websockify `127.0.0.1`，容器内进程互访，不落到宿主网卡。
3. **不暴露端口**：`docker-compose.yml` 仍只映射 `8790`。
4. x11vnc 本身用 `-nopw`（不叠 VNC 密码）——因为它从不对外，唯一入口已被面板鉴权兜住，再叠一层 VNC 密码没有额外收益且徒增管理成本。

## 测试

沿用现有 `tests/` 的 `fake_camoufox` + monkeypatch 风格：

- **TakeoverManager 生命周期**：注入假 `ProcessLauncher` + `fake_camoufox`，覆盖 `start` 全序列、`stop` 反序清理、`start` 时的子进程回滚、空闲超时触发 `stop`、单例互斥（第二次 start 抛忙）。
- **代理逻辑复用**：抽出的 `build_camoufox_kwargs` 单测，确认注册与接管走同一条代理/geoip 分支（含带认证 SOCKS5 起中继的路径）。
- **API 层**：`start`/`stop`/`status` 正常路径，以及 `401`（无 cookie）、`409`（忙）、`400`（无 sessionKey）、`404`（无此账号）、`403`（disabled）。
- **WS 桥鉴权**：无 cookie 连 `/vnc/websockify` 被拒。
- 真 Xvfb/x11vnc/websockify 不进 CI（self-hosted runner 也不跑），靠可注入接口隔离。

## 交付边界

- 新增：`server/takeover.py`、`/vnc/*` 静态与 WS 反代路由、账号列表「接管」按钮与状态区、`takeover` 配置段。
- 修改：`Dockerfile`（装 x11vnc/websockify/novnc）、`claude_register/browser.py`（抽出 `build_camoufox_kwargs`）、`server/app.py`（挂路由）、`config.example.yaml`/`config_store`（新增配置段）。
- `docker-compose.yml`：端口不变，无需改动。
