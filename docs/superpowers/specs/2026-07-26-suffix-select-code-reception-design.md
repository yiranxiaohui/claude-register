# 选后缀 + 自动接码 —— 设计文档

日期：2026-07-26

## 背景与问题

`claude-register` 目前跑 `uv run main.py` 的行为是：调用 `list_accounts(limit=100)`，把 AnyMail 里已存在的邮箱全部列出来让用户挑编号。实际使用时这个列表有 100 条，混着历史脚本留下的 `u_xxx@ckvlhj.xyz`、别的业务打了 tag 的邮箱（`claude批量注册`、`已封Claude`、`google`），用户要从里面翻——这不是想要的交互。

更关键的缺口：**整个流程没有接码**。现有 `run_browser` 只做到打开 claude.ai 登录页、填入邮箱、点 Continue、截图，然后 `input()` 停住等人手动操作。从来没有调用 AnyMail 的 `GET /api/emails/latest` 去取验证码。

用户要的流程是：**自己选后缀（域名）→ 系统按这个后缀创建邮箱 → 系统自动接码**。

## 目标

1. 默认流程改为「选后缀 → 系统建邮箱 → 自动接码 → 自动填码登录」一条命令跑完
2. 删掉列出 100 个旧邮箱的交互；复用旧邮箱走 `--email`
3. 邮箱前缀由系统随机生成，用户只决定后缀
4. 接码失败或填码框定位不到时，验证码和邮箱地址必须留在终端上，不能白跑

## 非目标

- 不做批量注册
- 不做 Gmail / Outlook（AnyMail 接码只支持 `provider=domain`）
- 不做无关重构，只拆动到的模块

## 架构

`flow.py` 现在 240 行，混了浏览器操作、邮箱选择交互、流程编排三件事。加接码只会更挤，因此拆开：

```
main.py                argparse 入口
claude_register/
  anymail.py           AnyMail API 客户端 + 新增 poll_code()
  mailbox.py    [新]    选后缀 + 建邮箱
  browser.py    [新]    Playwright：启动、开登录页、填邮箱、填验证码
  flow.py               瘦编排层
```

模块职责与依赖：

| 模块 | 职责 | 依赖 |
|---|---|---|
| `anymail.py` | 只跟 AnyMail HTTP API 通信 | httpx |
| `mailbox.py` | 决定用哪个后缀、建哪个邮箱 | `anymail.py` |
| `browser.py` | 只操作页面；验证码作为参数传入，不知道 AnyMail 存在 | playwright |
| `flow.py` | 唯一知道全局顺序的地方 | 以上三者 |

`browser.py` 不依赖 `anymail.py` 是有意的：填码函数签名是 `fill_code(page, code)`，验证码从哪来它不关心。这让浏览器层和接码层可以独立改、独立验证。

## 数据流

```
1. since = now(UTC)                          ← 必须在建邮箱之前
2. domain = choose_suffix(client, --domain)
3. mailbox = create_mailbox(client, domain)   随机前缀 claude_<8位hex>
4. 浏览器：开 claude.ai/login → 等表单（含 Cloudflare 轮询）→ 填邮箱 → 点 Continue
5. 浏览器：等验证码输入界面出现
6. code = client.poll_code(to=mailbox.email, since=since, regex=..., timeout=120)
7. 浏览器：填码 → 提交 → 确认已登录
```

### 第 1 步的位置是关键

`since` 必须在 `POST /api/accounts` **之前**记录。接码文档 §8.2：若用首次轮询时的 `now()` 当 `since`，会漏掉「建邮箱完成 → 首次轮询」窗口内到达的邮件，这段可能长达几百毫秒到几秒。这是个容易写错且很难复现的 bug，要钉一个测试。

## 接口设计

### `anymail.py` 新增

```python
def poll_code(
    self,
    *,
    to: str,
    since: str,
    code_regex: str | None = None,
    timeout: float = 120.0,
    interval: float = 3.0,
) -> str | None:
    """轮询 GET /api/emails/latest 直到取到验证码；超时返回 None。"""
```

- 轮询间隔 3 秒，超时 120 秒（文档 §4.2 推荐值）
- `code_regex` 交服务端提取，返回 `code` 字段
- 网络报错指数退避 1s → 2s → 4s，不因一次抖动整体失败
- 超时返回 `None` 而不是抛异常——调用方要走降级路径

### 接码正则与两级匹配

主正则默认 `code[^\d]*(\d{6})`，兜底正则 `\b(\d{6})\b`。文档 §8.4 建议用捕获组而非裸 `\d{6}`，避开邮件里的日期数字。可用 `ANYMAIL_CODE_REGEX` 覆盖主正则。

**两级匹配在单次请求内完成，不发两次请求**：每轮轮询带主正则请求一次；若返回了邮件但 `code` 为 `null`，就在客户端拿兜底正则去匹配返回的 `subject` / `text_body` / `html_body`。这样避免每轮翻倍的请求量，也避免「主正则没中就白等一整轮」。

### `mailbox.py`

```python
def choose_suffix(client: AnyMailClient, preferred: str | None = None) -> str:
    """--domain > ANYMAIL_DOMAIN > GET /api/domains 交互选择。只有一个域名不提示。"""

def create_for_suffix(client: AnyMailClient, domain: str) -> Mailbox:
    """随机前缀 claude_<8位hex> 建邮箱。409 撞名换前缀重试（现有逻辑）。"""
```

现有 `choose_domain` 逻辑基本可用，搬过来复用。`create_custom_mailbox` 里提示输入前缀的部分删掉——前缀改由系统生成。

### `browser.py`

```python
def launch_browser(p)
def open_login(page)
def wait_login_form(page, timeout_ms=120_000)
def fill_email(page, email)
def wait_code_screen(page, timeout_ms=60_000) -> bool
def fill_code(page, code) -> bool      # 定位不到返回 False，调用方降级
```

`fill_code` 返回 bool 而不抛异常，因为「填不进去」是预期内的降级路径，不是错误。

## 命令行与配置

```
uv run main.py                          选后缀 → 建邮箱 → 全自动接码登录
uv run main.py -d ckvlhj.xyz            直接指定后缀
uv run main.py -e old@ckvlhj.xyz        复用指定邮箱
uv run main.py --no-auto-code           只打印验证码，不自动填
uv run main.py --code-timeout 180       接码超时秒数，默认 120
```

删除 `--new`（默认就是新建，flag 无意义）。

### 参数交互规则

明确下来避免实现时二选：

- `-e` 与 `-d` 同时给 → `-e` 优先，忽略 `-d` 并打印一行提示（邮箱已含后缀，`-d` 无从生效）
- `-e` 指定的邮箱 → 走现有 `get_or_create_mailbox`：存在则复用，不存在则创建。`since` 同样在这之前记录
- `--no-auto-code` → 仍然轮询接码并打印验证码，只是不调 `fill_code`。浏览器保持打开
- `--code-timeout 0` → 完全跳过接码，只建邮箱 + 填邮箱（等于旧行为）

### `ANYMAIL_EXPIRES_HOURS` 取值

- 未设置或留空 → 用默认 24 小时
- 正数 → 该小时数
- `0` 或负数 → 永久（不传 `expires_at`）

`.env` 新增，均可选：

```
# ANYMAIL_CODE_REGEX=code[^\d]*(\d{6})     覆盖默认接码正则
# ANYMAIL_EXPIRES_HOURS=24                 邮箱有效期小时数，空=用默认 24
```

### 邮箱有效期

默认保持现有的 **24 小时过期**（用户明确选择保留）。`ANYMAIL_EXPIRES_HOURS` 可覆盖，留空则用默认 24。

已向用户说明的代价：24 小时后 AnyMail 的 cron 会删掉邮箱及其邮件，注册完的账号若之后要收改密码、设备验证的信，需在到期前延长有效期或改成永久。用户已知悉并选择 24 小时。

## 错误处理

原则：**只要验证码拿到手了，就绝不能因为后面填不进去而丢掉它。** 所有降级路径都保证邮箱地址和验证码打印在终端。

| 失败点 | 处理 |
|---|---|
| 无可用域名 | 明确报错：设 `ANYMAIL_DOMAIN`，或给 API Key 加 `domains:read` |
| 建邮箱 409 撞名 | 换随机前缀重试 5 次（现有逻辑） |
| Cloudflare 卡住 | 保留现有 `wait_login_form` 轮询，超时截图 `waiting_login.png` |
| 接码超时 | 截图 + 打印邮箱地址和 AnyMail 后台链接，浏览器保持打开，不删邮箱 |
| 填码框定位不到 | 大字打印验证码 + 截图，浏览器保持打开手填 |
| 验证码界面没出现 | `wait_code_screen` 返回 `False`：截图，仍然继续轮询接码并打印验证码（码本身有价值），浏览器保持打开 |
| 轮询网络抖动 | 指数退避 1s→2s→4s，不中断流程 |
| API Key scope 不足（403） | 打印 AnyMail 返回的 error 原文 + 需要的 scope 清单 |

浏览器默认保持打开（`CLAUDE_REGISTER_NO_PAUSE=1` 可跳过暂停），失败时尤其不关。

**邮箱一律不自动删除。** 接码文档 §5.3 提到可以 `DELETE /api/accounts/:id` 回收，但这里注册出来的账号要长期用，删邮箱等于丢掉后续所有来信。清理交给 24 小时 `expires_at` 的 cron，或按 `tag=claude-register` 手动批量清理（文档 §5.4）。

## 测试策略

### 自动测试（`tests/`，`respx` mock httpx）

- `poll_code`：首轮命中 / 第三轮命中 / 一直空到超时返回 `None` / 捕获组取第 1 组 / 网络报错退避后恢复 / 主正则未命中时客户端兜底正则接手
- **`since` 时序**：断言请求里的 `since` 早于建邮箱的时刻（钉住文档 §8.2 那个坑）
- `choose_suffix`：`--domain` 优先 / 回落 `ANYMAIL_DOMAIN` / 回落 `/api/domains` / 单域名不提示 / 无域名报错
- `create_for_suffix`：409 重试换前缀 / `expires_at` 按 24 小时算对 / `ANYMAIL_EXPIRES_HOURS=0` 时不传 `expires_at`
- 默认正则：能从 `Your login code is 123456` 取出 `123456`，不误取日期里的 6 位数
- 参数交互：`-e` 与 `-d` 同时给时 `-e` 胜出 / `--code-timeout 0` 跳过接码

### 必须实跑验证

- claude.ai 填邮箱、点 Continue 的选择器仍有效（现有代码在用）
- **验证码输入界面的选择器** —— 最大未知项。可能是单个 input，也可能是 6 个分开的 OTP 格子，两种填法不同
- 端到端跑通一次真实注册

实现时先跑到验证码界面，截图 + dump DOM 看清结构再写选择器，不猜。降级路径保证这步没搞对也不白跑。

### 依赖

`pyproject.toml` 加 dev 依赖：`pytest`、`respx`。

## 已知风险

1. ~~**验证码输入框结构未知**~~ —— **已由 Task 6 实测解决**（2026-07-26）。结论：单个 `input`，`data-testid="code"`；且 `fill_email` 之后先落在一个 **0 个 input 的中间态**，必须先点 `data-testid="enter-code"` 才会出现输入框。详见 `docs/superpowers/notes/2026-07-26-code-screen-dom.md`。
2. **提交验证码可能弹 hCaptcha 拖拽验证** —— Task 6 新发现，本设计原先完全没预料到。用假码实测必弹（`api.hcaptcha.com/getcaptcha`），真码是否必然弹**尚未验证**。已决定的处理方式：**不尝试自动绕过**，检测到就打印横幅提示 + 截图 + 保留浏览器，由人工拖拽完成。这意味着"全自动登录"在最后一步可能仍需人工介入一次。
3. **claude.ai 可能改版** —— 选择器失效。缓解：`data-testid` 比文案稳；另留 `autocomplete="one-time-code"` 和 `aria-label="Login code"` 两条兜底定位；失败都留截图。
4. **Cloudflare 等待时长波动大** —— Task 6 实测 33 秒到 120 秒以上，有一次直接撞上 `wait_login_form` 自身的 120 秒超时。缓解：现有轮询会打印进度；超时留截图。
5. **24 小时过期** —— 用户已知情选择，见上文。
