# claude-register

选一个邮箱后缀，系统自动建邮箱、打开 Claude 登录页填入，并自动收取登录邮件完成登录。

## 准备

1. 复制 `.env.example` 为 `.env`，填写 AnyMail 配置
2. 安装依赖：

```text
uv sync
uv run camoufox fetch
```

AnyMail API Key 需要的 scope：`emails:read` + `accounts:write`（若没固定 `ANYMAIL_DOMAIN`，再加 `domains:read`），并限定账号类型为 `Domain`。

## 启动

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

**浏览器引擎是 Camoufox（Firefox 系隐身浏览器），不是 Chromium。** 它以
`headless="virtual"` 模式运行——自动包一层 Xvfb 虚拟显示，既适配无图形界面的
服务器/容器，也比真 headless 更能扛住 Cloudflare 挑战。运行前需装好 Xvfb，并跑过
一次 `uv run camoufox fetch` 下载浏览器二进制。

**虚拟显示下你看不到浏览器实时画面。** 关键步骤会截图到 `output/`。默认的魔术链接
路径全程无需人工实时交互，不受影响；但如果走到验证码那条路弹出了 hCaptcha 拖拽题，
在没有图形界面的机器上就没法手动拖拽——需要换到带显示的环境，或接 VNC。

**Cloudflare。** claude.ai 前面有 Cloudflare 挑战，实测等待时长在 30 秒到 120 秒以上之间波动，有时会直接超时，偶尔放行后还会返回一个完全空白、没有任何输入框的页面。这不是脚本的问题，重试或换个时间即可。超时会自动截图到 `output/waiting_login.png`。

**hCaptcha。** 如果走验证码那条路，点提交后可能弹出 hCaptcha 拖拽验证。程序**只检测、不尝试绕过**——检测到会打印提示并保留浏览器，需要你手动拖拽完成。魔术链接这条路不经过这一步。

**邮箱 24 小时后被清理。** 默认 `expires_at` 是 24 小时，到期后 AnyMail 的 cron 会把邮箱连同邮件一起删除。用它注册的账号如果之后还要收信（改密码、设备验证），请在到期前延长有效期，或设 `ANYMAIL_EXPIRES_HOURS=0` 建成永久邮箱。

**本工具不会自动删除邮箱。** 要清理就按 tag 批量删：`GET /api/accounts?tag=claude-register`。

## 测试

```text
uv run pytest tests/ -v
```

62 个测试，约 1 秒跑完。覆盖：登录链接提取与收件人校验、验证码轮询与退避、`since` 时序不变量、后缀选择、环境变量解析。

浏览器层（`browser.py`）没有单测——给 Playwright 页面交互写 mock 成本高、价值低。实际跑通验证过的只到：建邮箱、从真实邮件里提取登录链接并比对收件人、在登录页填入邮箱这一步。再往后的页面交互——`wait_code_screen`、`fill_code`、`open_magic_link`、`hcaptcha_visible`——目前一次完整流程都没跑通过：claude.ai 这边已经连续卡了几个小时的 Cloudflare 挑战或空白页，没能撑到魔术链接真正打开的那一步。这部分现在只有单测和抓取到的 DOM 快照兜底，不代表代码有问题，只是还没有实跑验证。
