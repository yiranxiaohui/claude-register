# claude-register

选一个邮箱后缀，系统自动建邮箱、打开 Claude 登录页填入，并自动收取登录邮件完成登录。

## 准备

CLI 方式（本地跑 `main.py`）：

1. 复制 `.env.example` 为 `.env`，填写 AnyMail 配置
2. 安装依赖：

```text
uv sync
uv run camoufox fetch
```

AnyMail API Key 需要的 scope：`emails:read` + `accounts:write`（若没固定 `ANYMAIL_DOMAIN`，再加 `domains:read`），并限定账号类型为 `Domain`。

`.env` 环境变量仍然兼容，但**推荐改用 `config.yaml`**（见下方「Web 面板部署」）——CLI 也支持 `uv run main.py --config config.yaml` 直接读同一份配置文件，无需再维护两套配置。

## Web 面板部署

项目自带一个 Web 管理面板（登录、触发注册、实时日志、历史/账号列表、设置项），用 Docker 部署：

1. 复制 `config.example.yaml` 为 `config.yaml`，填写 `anymail` 段（API Key / base_url / domain）和 `panel.password`（面板登录密码）
2. 拉镜像并启动：

```text
docker compose pull
docker compose up -d
```

3. 浏览器打开 `http://<部署机器 IP>:8790`，用 `config.yaml` 里的 `panel.password` 登录

`docker-compose.yml` 默认把 `./data`（sqlite + 运行记录）和 `./config.yaml` 挂进容器；镜像由 CI（`.github/workflows/build.yml`，跑在 self-hosted runner 上）构建推送到 `ghcr.io/yiranxiaohui/claude-register`，**部署机器只 `pull`，不在本地跑 `docker build`**。

面板里改的设置会写回 `config.yaml`，接口和日志里 AnyMail Key 等敏感字段做了脱敏。

## Xpra HTML5 免密登录接管

账号列表里凡抓到 `sessionKey` 的账号，都可点「接管」：后台会用这份 Cookie 开一个
已登录 claude.ai 的浏览器，并通过 Xpra HTML5 让你在网页上实时接管操作。

- Xpra 直接管理独立虚拟桌面并提供 HTML5/WebSocket，不再使用 VNC/RFB 协议；
  客户端自带 ping 保活和自动重连，连续恢复失败时外层接管页也会自动重载画面。
- 容器内 Nginx 直接反代 Xpra，并用 FastAPI `auth_request` 复用面板密码鉴权；
  **不额外对外开端口**（Xpra 仅绑 localhost，RFB/VNC 明确关闭）。
- 双向文本剪贴板默认开启。使用 HTTPS 的 Chrome/Edge 时可直接在本机和接管浏览器之间
  复制粘贴；首次使用需允许浏览器访问剪贴板。HTTP 或限制 Clipboard API 的浏览器可用
  Xpra 右上角菜单里的剪贴板作为兜底。
- 同一时刻只允许一个接管会话；接管页会每 20 秒续期，页面关闭或断网后默认
  15 分钟自动结束（`config.yaml` 的
  `takeover.idle_timeout_min` 可调，`takeover.enabled: false` 可整体关闭）。
- 接管会话用独立的虚拟屏（`:100`，Xpra desktop），与注册流程并行、互不影响。
- sessionKey 是会话级凭证，换环境或过期会失效。接管画面落回登录页时，可在账号页的
  接管提示条点击「重新自动登录」：系统会在当前接管浏览器里重新提交邮箱、收取并打开
  新的登录链接，成功后自动回写 sessionKey；如果账号确实被封则会明确报错且不会循环重试。

## 开放 API（触发注册、按需导出）

供外部脚本调用，与面板登录互相独立。

**接口文档**（免鉴权，可直接把地址发给调用方或 AI；文档里的地址会按访问域名自动填好）：

| 地址 | 用途 |
|---|---|
| `/api/v1/docs.md` | 完整接口文档（Markdown）：鉴权、参数、响应、字段表、错误码、bash/Python 示例 |
| `/api/v1/openapi.json` | OpenAPI 3.1 规范，可导入 Postman / Apifox 或供 AI 工具生成调用代码 |
| `/api/v1/docs` | Swagger UI 在线调试 |
| `/llms.txt` | 面向 AI 的入口索引，指向上面三份文档 |

文档正文维护在 `server/open_api.md`。接口返回 401/403 时，错误信息里也会带上文档地址。
在面板「系统设置 → 开放 API」打开开关并保存，再点
「生成」得到 API Key（生成即生效；重新生成后旧 Key 立即失效）。请求头任选其一：

```
Authorization: Bearer <key>
X-API-Key: <key>
```

不接受把 Key 放在 URL 参数里（会落进访问日志）。未启用返回 403，Key 错误返回 401。

| 接口 | 说明 |
|---|---|
| `POST /api/v1/register` | 触发一次注册。body 可选 `email`（指定邮箱）、`domain`、`proxy_id`（代理池里的 id）。返回 `202 {"run_id", "status": "running"}`；已有任务在跑返回 `409 {"active_run_id"}` |
| `GET /api/v1/register/{run_id}` | 查询结果。`wait`（0–50 秒）长轮询；`fields` 选择返回字段；`format=text/csv/line` 时额外返回 `export` 文本（`line` 可用 `sep` 指定分隔符，默认 `----`） |
| `GET /api/v1/accounts/export` | 批量导出。`fields`、`format`（`json` 默认 / `text` / `csv` / `line`）、`sep`，过滤：`status`（`success`/`needs_manual`）、`check_status`（`alive`/`dead`/…）、`claimed`（`true`/`false`）、`emails`（逗号分隔）。响应头 `X-Total-Count` 为条数 |
| `POST /api/v1/accounts/claim` | 获取一个账号：每次返回一个注册成功、有 sessionKey、未获取过的账号（默认跳过检测为 `dead`/`blocked` 的，`check_status=alive` 可只取有效的），并标记为「已获取」，不会重复发放。支持 `fields`/`format`/`sep`；返回 `account`、`claimed_at`、`remaining`；没有可用账号返回 `404`。面板账号列表显示「已获取」标签，可在「编辑」里取消标记 |
| `GET /api/v1/fields` | 可选字段与格式 |
| `GET /api/v1/proxies` | 代理池里的 `id` 与名称（不返回地址和凭据） |

注册结果 `status`：`running` 进行中、`success` 成功（拿到 sessionKey）、`needs_manual` 流程跑完但没拿到
sessionKey（账号已入库，可在面板接管）、`failed` 失败；后两者附带最近日志 `log_tail`。

可选字段（`fields`，逗号分隔，按给出的顺序输出）：`email`、`session_key`、`proxy`、`mail_base_url`、
`mail_key`、`password`、`display_name`、`domain`、`status`、`check_status`、`checked_at`、`created_at`、
`claimed_at`、`last_run_id`。默认是前五项，`text` 格式与面板「导出」的默认输出一致。

示例：触发注册 → 等到完成 → 取邮箱 + sessionKey + 密码：

```bash
KEY=cr_xxx; BASE=http://10.1.42.1:8790/api/v1
RID=$(curl -s -X POST -H "Authorization: Bearer $KEY" $BASE/register | jq .run_id)
while :; do
  R=$(curl -s -H "Authorization: Bearer $KEY" \
    "$BASE/register/$RID?wait=50&fields=email,session_key,password&format=line")
  [ "$(echo "$R" | jq -r .status)" != running ] && break
done
echo "$R" | jq -r '.status, .export'   # success / email----sessionKey----password
```

批量导出所有检测有效的账号为 CSV：

```bash
curl -H "Authorization: Bearer $KEY" \
  "$BASE/accounts/export?format=csv&check_status=alive&fields=email,session_key,proxy" -o alive.csv
```

面板账号页的「导出…」同样可以勾选字段、选格式和范围，并记住上次的选择。

## 启动（CLI）

选后缀 → 建邮箱 → 自动登录：

```text
uv run main.py
```

直接指定后缀（跳过交互选择）：

```text
uv run main.py -d example.com
```

复用已有邮箱：

```text
uv run main.py -e you@example.com
```

`-e` 与 `-d` 同时给时 `-e` 优先（邮箱本身已含后缀）。

只打印不自动打开 / 调整等待超时 / 跳过等待邮件：

```text
uv run main.py --no-auto-login
uv run main.py --login-timeout 180
uv run main.py --login-timeout 0
```

`--no-auto-code` 和 `--code-timeout` 是旧名别名，仍然可用。

## 登录方式：魔术链接，不是验证码

实测约 40 封真实 Claude 登录邮件，**全部是魔术链接，没有 6 位验证码**：

```text
标题：Secure link to log in to Claude.ai | <时间>
正文：https://claude.ai/magic-link#<32位hex>:<base64(收件邮箱)>
```

所以默认路径是「等登录链接 → 自动在浏览器里打开它」。链接尾部的 base64 解出来就是收件邮箱，程序会拿它和目标邮箱比对，**解不出或对不上都会跳过**，避免拿错邮箱的链接去登录。

程序仍保留了 6 位验证码这条路（`poll_code` / `fill_code`）：Claude 的登录界面上确实存在验证码输入框（点 "Enter verification code" 后出现），只是这些临时邮箱收到的是链接邮件。等链接超时后会再试验证码作为兜底，兜底时长是 `--login-timeout` 总预算的 25%（最多 30 秒），不会让总等待时间超过你设的值。

## 已知的坑

**浏览器引擎是 Camoufox（Firefox 系隐身浏览器），不是 Chromium。** headless 档位
按平台自动选，不用手动改代码：

| 平台 | 档位 | 说明 |
| --- | --- | --- |
| Linux + 装了 Xvfb | `headless="virtual"` | 自动包一层 Xvfb 虚拟显示，适配无图形界面的服务器/容器 |
| Windows / macOS | `headless=False` | 桌面本来就是真显示器，会弹出真实浏览器窗口 |
| Linux 无 Xvfb | `headless=True` | 兜底，能跑但指纹弱一档，更容易被 Cloudflare 拦 |

前两档都比真 headless 更能扛住 Cloudflare 挑战。`virtual` 这一档**只有 Linux 能用**
——它本质是 X11 的虚拟帧缓冲，而 Windows 上的 `camoufox.exe` 是原生 Win32 构建，
不走 X11，装 Xvfb 也没用。运行前都需要跑过一次 `uv run camoufox fetch` 下载浏览器二进制。

**虚拟显示下你看不到浏览器实时画面。** 这条只对 Linux 的 `virtual`/`headless` 档成立；
Windows/macOS 上有真窗口，能直接看到页面。关键步骤在所有平台都会截图到 `output/`。
默认的魔术链接路径全程无需人工实时交互，不受影响；但如果走到验证码那条路弹出了
hCaptcha 拖拽题，在没有图形界面的机器上就没法手动拖拽——需要换到带显示的环境，或接 VNC。

**Cloudflare。** claude.ai 前面有 Cloudflare 挑战，实测等待时长在 30 秒到 120 秒以上之间波动，有时会直接超时，偶尔放行后还会返回一个完全空白、没有任何输入框的页面。这不是脚本的问题，重试或换个时间即可。超时会自动截图到 `output/waiting_login.png`。

**hCaptcha。** 如果走验证码那条路，点提交后可能弹出 hCaptcha 拖拽验证。程序**只检测、不尝试绕过**——检测到会打印提示并保留浏览器，需要你手动拖拽完成。魔术链接这条路不经过这一步。

**邮箱默认永久保留。** 默认不设 `expires_at`，注册出来的账号邮箱不会被 AnyMail 的到期 cron 清掉（之后还能收改密码、设备验证等邮件）。如果想让邮箱限时自动清理，设 `ANYMAIL_EXPIRES_HOURS=<正数小时>`，到期后 AnyMail 会把邮箱连同邮件一起删除。

**本工具不会自动删除邮箱。** 要清理就按 tag 批量删：`GET /api/accounts?tag=claude-register`。

## 测试

```text
uv run pytest tests/ -v
```

62 个测试，约 1 秒跑完。覆盖：登录链接提取与收件人校验、验证码轮询与退避、`since` 时序不变量、后缀选择、环境变量解析。

浏览器层（`browser.py`）的页面交互以真实 DOM/截图为据，配有轻量假页面单测（见 `tests/test_onboarding.py`）。已实跑验证到：建邮箱 → 填登录邮箱 → 收魔术链接 → `open_magic_link` 打开后落到 `claude.ai/onboarding` 建号页。后续 `finish_after_auth` 会勾选服务条款并点 Create account；建号后若还有名字输入会尝试填默认值。验证码路径上的 hCaptcha 仍只检测不绕过，需人工拖拽。
