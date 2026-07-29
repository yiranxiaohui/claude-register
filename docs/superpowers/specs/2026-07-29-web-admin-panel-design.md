# claude-register Web 管理面板 — 设计

日期：2026-07-29

## 背景与目标

claude-register 目前是纯 CLI（`uv run main.py`），配置全靠 `.env` 环境变量，日志只打到终端和 `output/` 截图，无持久化、无远程控制。本设计给它加一层 Web 管理面板，形态照搬 grok-register（FastAPI + React + Docker + GitHub Actions），实现三件事：

1. **网页看日志** —— 每次运行的日志/截图存档，并能实时看当前运行进度。
2. **网页控制脚本启动** —— 手动点「开始注册」触发一次运行（暂不做定时）。
3. **配置文件替代 env** —— 用 `config.yaml` 取代 `.env`，且网页可编辑。

CLI 原路保留：`uv run main.py` 照样能用，Web 只是加壳，共享同一套 `flow.run()`。

**明确不做（本期）：** 定时/自动运行；session（cookie）导出；并发多任务。

## 整体架构

```
claude-register/
├── claude_register/          # 现有 CLI 核心（复用）
│   ├── flow.py, browser.py, anymail.py, mailbox.py
│   ├── config.py             # 改造：数据来源 env → 配置对象
│   └── console.py            # 改造：log/banner 支持 contextvar sink
├── server/                   # 新增 FastAPI 后端
│   ├── app.py                # 路由 + 静态托管
│   ├── config_store.py       # config.yaml 读写 + 热更新
│   ├── db.py                 # SQLite（runs / accounts）
│   ├── runner.py             # 后台单任务执行 + 日志捕获
│   └── auth.py               # 密码登录
├── web/                      # 新增 React (Vite + bun) 前端
├── config.yaml               # 替代 .env（挂 data 卷）
├── data/claude-register.db   # SQLite（挂 data 卷）
├── data/runs/<run_id>/       # 每次运行的 log.txt + 截图（挂 data 卷）
├── Dockerfile                # 多阶段
└── .github/workflows/        # 构建推 ghcr.io/yiranxiaohui/claude-register
```

- 后端 FastAPI，前端 React 打包成静态资源由 FastAPI 一起托管（单端口，默认 `:8790`，与 grok-register 的 `:8788` 错开）。
- 持久化统一在 `data/` 卷：SQLite 单文件 + 每次运行一个目录（日志/截图）。

## 配置文件

`config.yaml` 替代 `.env`，挂在 data 卷，网页可编辑：

```yaml
panel:
  password: "改我"          # 面板登录密码；网页脱敏显示，留空表示不改
  port: 8790
anymail:
  api_key: "..."
  base_url: "https://..."
  domain: ""               # 固定后缀，空则每次选/传
  expires_hours: 24        # 邮箱有效期，0=永久
register:
  login_timeout: 120       # 等登录邮件总预算（秒）
  auto_login: true
  code_regex: ""           # 留空用内置默认正则
```

- 启动时读入内存对象；`config.py` 现有 `resolve_expires_hours` / `resolve_code_regex` 校验逻辑复用，只把数据源从 `os.getenv` 换成该对象。
- `config_store.py` 负责 yaml 读写 + 内存热更新。网页保存时：写回 yaml + 刷新内存；密码字段传空即「不修改」，返回前端时脱敏为 `••••`。
- `.env`/`load_dotenv` 兼容保留一个过渡期（config.yaml 缺失时可回落 env），但文档改为推荐 config.yaml。

## 数据模型（SQLite）

```
runs(
  id            INTEGER PK,
  email         TEXT,
  domain        TEXT,
  status        TEXT,   -- running / success / failed / needs_manual
  started_at    TEXT,
  finished_at   TEXT,
  output_dir    TEXT    -- data/runs/<id>
)

accounts(
  email       TEXT UNIQUE,
  domain      TEXT,
  created_at  TEXT,
  expires_at  TEXT,
  mailbox_id  TEXT,
  last_run_id INTEGER,
  status      TEXT
)
```

- 日志逐行写 `data/runs/<id>/log.txt`，截图也进该目录，静态托管给前端；`runs` 表只存元数据（大块非结构化数据不塞进 DB 行）。
- 每次成功注册按 `email` upsert 一条 account，`last_run_id` 指向最近运行。

## 后台执行与实时进度

**单任务锁：** 全局一把内存 `threading.Lock` + `runs` 表最多一条 `running`。点「开始注册」时若已有任务在跑 → 返回 409，前端禁用按钮。

**后台线程执行：** 注册在后台线程里跑现成的同步 `flow.run()`（Playwright sync API 不能在 asyncio 事件循环内直接跑，用线程隔离）。

**收尾改造：** `pause_for_user()` 在 Web 模式强制跳过（复用已有 `CLAUDE_REGISTER_NO_PAUSE=1`）。原「浏览器保持打开等回车」改为跑完即关、状态落库。

**日志捕获（contextvar sink）：**
- `console.py` 增加一个 `contextvars.ContextVar` 存当前 sink。`log()`/`banner()`：sink 为空 → 照常 `print`（CLI）；有 sink → 推给 sink（Web）。此改动不触碰任何调用点。
- runner 起线程前用 `contextvars.copy_context()` 绑定本次 run 的 sink。sink 两件事：① append 到 `log.txt`；② push 进内存队列供 SSE 消费。

**实时进度用 SSE（单向推日志，比 WebSocket 简单）：**
- `GET /api/runs/{id}/stream` → `text/event-stream`，从队列吐新日志行；断线重连时先补发 `log.txt` 已有内容再续流。
- 截图产生时推 `event: screenshot` 带文件名，前端就地显示。
- 跑完推 `event: done` 带最终状态。

**重跑某邮箱（-e 复用）：** 账号列表每行一个「重跑」按钮 → 用该 email 起新 run，等价 `flow.run(email=...)`。

## REST API

均需登录（除登录本身）：

```
POST /api/login                     密码 → 下发 session cookie
GET  /api/config                    读配置（密码脱敏）
PUT  /api/config                    保存配置（写回 yaml + 热更新）
POST /api/runs                      开始注册 {email?, domain?} → run_id；已有 running 返回 409
GET  /api/runs                      运行历史（分页）
GET  /api/runs/{id}                 单次详情 + 日志全文
GET  /api/runs/{id}/stream          SSE 实时日志
GET  /api/accounts                  账号列表
POST /api/accounts/{email}/rerun    用该邮箱重跑
静态：/runs/<id>/<png>              截图
静态：/                             前端
```

**认证：** 密码存 config，登录后下发 httponly session cookie；后端简单校验（单用户，签名 token 即可）。

## 前端（React + Vite，bun 构建）

三个页面，风格对齐 grok-register 简洁后台，不追求花哨：

- **登录页**：密码框。
- **主面板**：顶部「开始注册」（可选后缀 / 填已有邮箱）+ 当前运行实时日志面板（含截图缩略图）；下面两个 tab —— 运行历史、账号列表（带「重跑」）。
- **设置页**：配置表单，密码脱敏。

## 错误处理

- AnyMail Key 错 / 网络错 → run 落 `failed`，错误进日志，前端红色状态。
- Cloudflare 超时 / 没收到邮件 / 需人工 hCaptcha → 落 `needs_manual`（黄色），保留截图，日志里给 AnyMail 后台链接。
- 容器重启时若有残留 `running` run → 启动时标记为 `failed`（进程已死，不可恢复）。

## Docker / CI

**Dockerfile 多阶段：**
1. bun 构建前端（`web/` → 静态资源）。
2. uv 装后端依赖 + `camoufox fetch`。
3. 运行镜像含 Xvfb，`CMD` 起 uvicorn，挂 `data/` 卷。

**CI：** 在自托管 GitHub runner（LXC 1001）上 build 并推 `ghcr.io/yiranxiaohui/claude-register`。遵守操作规约「线上只 pull 不 build」——线上机器只 `docker compose pull && up -d`。

## 测试

- config 读写 / 密码脱敏 / 校验复用。
- SQLite CRUD（runs、accounts upsert）。
- 单任务锁并发拒绝（第二次 POST /api/runs 返回 409）。
- SSE 补发 + 续流。
- log sink 路由（CLI print vs Web 队列）。
- 浏览器流程本身沿用现有实现，不新增 e2e。

## 分单元职责

- `config_store.py`：只管 yaml ↔ 内存对象 + 脱敏，不碰 DB/浏览器。
- `db.py`：只管 SQLite，纯数据访问，无业务逻辑。
- `runner.py`：编排一次 run（锁、线程、sink 绑定、状态落库），依赖 `flow.run` + `db` + `console` sink。
- `auth.py`：只管密码校验 + cookie。
- `app.py`：路由 + 静态托管，薄，把活派给上面四个。
- 现有 `claude_register/*` 保持 CLI 可独立运行，Web 层不侵入其内部逻辑（仅 config.py 换数据源、console.py 加 sink 钩子）。
