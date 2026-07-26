# claude.ai 验证码界面 DOM

日期：2026-07-26（探针：`scripts/probe_code_screen.py`）

邮箱：`claude_a76cb70c@ckvlhj.xyz`（本次最终成功探到验证码输入框的那次运行所建）。
探索过程中另外产生了一次 `claude_aafdbe25@ckvlhj.xyz`（第一次运行 + 复用同一邮箱做了三次补充观察），
两个邮箱都来自同一域名 `ckvlhj.xyz`，都只用来探路，未真正拿验证码登录过。

**重要发现（与 brief 假设不同）：** brief 里的探针脚本 `fill_email` 之后立刻 `wait_for_timeout(5s)` 就
dump DOM，这时候页面还停在一个**中间态"提示"界面**（"To continue, click the link sent to
{email}"），这个界面上 **0 个 input**，只有三个按钮：`Enter verification code` / `Resend email` /
`Use a different email`。必须点击 `data-testid="enter-code"` 这个按钮，才会展开真正的验证码输入框。
Task 7 写 `wait_code_screen`/`fill_code` 时必须先处理这一步，否则会在 0-input 的界面上直接超时。

## 1. 是单个输入框还是多个 OTP 格子？

**单个输入框。** 点击 "Enter verification code" 之后，页面上只出现 **1 个 `<input>`**（对应 `page.locator("input").all()` 返回长度为 1，不是 6 个分离的 OTP 格子）。它的属性：

```
{'type': 'text', 'name': '', 'id': 'code', 'placeholder': 'Enter verification code',
 'autocomplete': 'one-time-code', 'maxLength': -1, 'inputMode': 'numeric',
 'ariaLabel': 'Login code', 'dataTestId': 'code'}
```

对应原始 HTML（从 `output/code_screen_real.html` 摘录）：

```html
<input data-cds="TextInput" data-size="lg" data-testid="code" aria-label="Login code"
       placeholder="Enter verification code" inputmode="numeric"
       autocomplete="one-time-code" data-1p-ignore="true" class="..." />
```

点击 "Enter verification code" 之前的中间态界面上是 **0 个 input**（`output/code_screen.html`
里字面量 `<input` 出现 0 次），只有按钮，所以那个界面不需要也不能在其上直接找输入框——必须先点
`enter-code` 按钮把表单换成上面这个单输入框的验证码表单。

页面上没有发现隐藏字段、蜜罐字段或无关的 input——因为验证码表单展开前后页面上除了这一个 `#code`
之外压根没有别的 `<input>` 标签（搜索栏等都是别的元素，不是 input）。

## 2. 最稳的定位方式？

`data-testid="code"` 最稳，其次 `aria-label="Login code"`，二者与 `autocomplete="one-time-code"`、
`placeholder="Enter verification code"` 完全一致，指向同一个元素，没有歧义。

实际可用的 Playwright 表达式（任选其一，推荐第一个）：

```python
page.get_by_test_id("code")
# 等价：
page.locator('input[data-testid="code"]')
# 等价：
page.get_by_label("Login code")
```

提交按钮同理，最稳的是 `data-testid="continue"`：

```python
page.get_by_test_id("continue")
```

（按钮可见文字是 `Verify Email Address`，但文字可能因 A/B 或文案调整而变，`data-testid` 更稳。）

展开验证码表单前，需要先点的按钮是：

```python
page.get_by_test_id("enter-code")
```

## 3. 填完要不要点提交按钮？

**需要点提交按钮，不会自动提交。** 实测：在 `#code` 输入框里键入假验证码 `000000`（不满足真实
6 位有效码，但长度符合预期），等待 3 秒，用 Playwright 的 `page.on("request", ...)` 监听所有
POST / 含 `verif`/`login` 关键字的请求：

- 输入完、点击提交按钮前：只捕获到埋点请求（`event_logging/v2/batch`、Datadog RUM），**没有任何
  验证码校验相关的请求**，说明输入满 6 个字符不会自动提交。
- 提交按钮 `disabled` 属性输入前后均为 `False`（本来就没被禁用，跟输入内容无关，不能用它判断"是否
  填满"）。
- 点击 `data-testid="continue"` 按钮之后，才捕获到 `a-cdn.claude.ai/fc/gt2/public_key/...` 与
  `api.hcaptcha.com/getcaptcha/...` 这类请求，并且页面弹出了一个 **hCaptcha 拖拽验证弹窗**（"Drag
  the vial to the empty slot it fits into"）。这说明提交动作确实需要显式点击按钮触发，而且
  Anthropic 在提交验证码这一步会现场加验一个 hCaptcha 拖拽题——这是本次探索中新发现的、brief 未
  预期到的一个环节，**Task 7 需要额外考虑"提交后可能弹出 hCaptcha"这件事**（本笔记只负责如实记录，
  不负责解决它）。因为填的是假验证码，无法确认 hCaptcha 是否每次都出现，还是仅对可疑/重复提交触发；
  这一点标记为**未验证**，建议 Task 7 用真实验证码测试时留意。

结论：`fill_code` 里需要显式 `page.get_by_test_id("continue").click()`，不能只填完就等。

## 4. 怎么判断「验证码界面已出现」？

`wait_code_screen` 应该做两级等待：

1. 先等中间态界面上的 `data-testid="enter-code"` 按钮出现并点击它：
   ```python
   page.get_by_test_id("enter-code").wait_for(state="visible", timeout=...)
   page.get_by_test_id("enter-code").click()
   ```
2. 再等真正的验证码输入框出现：
   ```python
   page.get_by_test_id("code").wait_for(state="visible", timeout=...)
   ```
   （等价写法：`expect(page.get_by_test_id("code")).to_be_visible(...)`）

第 2 步的元素就是"验证码界面已出现"的判定依据——`input[data-testid="code"]` 可见。

## 原始属性 dump

### 第一次探针（严格按 brief 脚本跑，`fill_email` 后 5 秒立即 dump）

邮箱：`claude_a76cb70c@ckvlhj.xyz`（`prepare_mailbox(client, domain="ckvlhj.xyz")` 新建）

命令：

```
uv run scripts/probe_code_screen.py < /dev/null
```

终端输出（UTF-8 捕获，`chcp 65001` + `PYTHONIOENCODING=utf-8`）：

```
已创建邮箱：claude_a76cb70c@ckvlhj.xyz
邮箱：claude_a76cb70c@ckvlhj.xyz  since=2026-07-26T12:54:43.948880Z
已启动本机 Chrome（channel=chrome）
正在打开：https://claude.ai/login
页面标题：Just a moment...
当前地址：https://claude.ai/login?__cf_chl_rt_tk=...
等待登录表单… 0s 标题='Just a moment...' url=...
（每 3 秒轮询一次，持续到 117s 仍是 'Just a moment...'，触发 120s 超时）
截图已保存：output/waiting_login.png
RuntimeError: 登录表单未出现（可能卡在 Cloudflare 验证页）。已截图：output/waiting_login.png
```

这次运行 Cloudflare 卡了超过 120 秒（比 brief 里提到的"约 33 秒"明显更久，属于波动），触发了
`wait_login_form` 自身的超时保护，不算探针失败——这是 Cloudflare 本身的等待时长波动，任务前一次
用同一域名的运行里也出现过（见下面"第一次成功探到验证码界面"里也等了将近 60-120 秒不等）。

首次真正跑通、产出 `output/code_screen.png` + `output/code_screen.html` 的是另一个邮箱
`claude_aafdbe25@ckvlhj.xyz`（同一批探索里更早创建的），日志：

```
邮箱：claude_aafdbe25@ckvlhj.xyz  since=2026-07-26T12:34:18.338452Z
已启动本机 Chrome（channel=chrome）
正在打开：https://claude.ai/login
页面标题：Just a moment...
（等待登录表单… 0s~57s，标题从 'Just a moment...' 变为 'Loading ...' 再变为 'Claude'）
登录表单已出现。
已填入邮箱：claude_aafdbe25@ckvlhj.xyz
已点击 Continue with email
截图已保存：output/code_screen.png
DOM 已保存：output/code_screen.html

页面上有 0 个 input：
```

即：严格按 brief 脚本跑完，落地页面是"提示已发链接"的中间态，**0 个 input**，这正是本笔记开头
提到的与 brief 假设不同之处。

### 补充探针（点击 "Enter verification code" 之后）

为了回答四个问题，追加了一版临时脚本（未保留为交付物，仅用于本次调查；逻辑等价于对同一 `browser.*`
函数多做一步 `enter-code` 点击），复用邮箱 `claude_aafdbe25@ckvlhj.xyz`（`prepare_mailbox(client,
email=...)`，未创建新邮箱）。点击 `data-testid="enter-code"` 之后：

```
已点击 Continue with email
已点击 Enter verification code
截图已保存：output/code_screen_real.png
DOM 已保存：output/code_screen_real.html

页面上有 1 个 input：
  [0] {'type': 'text', 'name': '', 'id': 'code', 'placeholder': 'Enter verification code',
       'autocomplete': 'one-time-code', 'maxLength': -1, 'inputMode': 'numeric',
       'ariaLabel': 'Login code', 'dataTestId': 'code'}

页面上有 28 个 button（节选与验证码相关的几个）：
  [14] text='Verify Email Address' {'type': 'submit', 'id': '', 'ariaLabel': None,
       'dataTestId': 'continue', 'disabled': False}
  [15] text='Try sending again' {'type': 'button', ...}
  [16] text='Change email address' {'type': 'button', ...}
```

（其余 25 个 button 是页面顶部导航、价格卡片、FAQ 折叠项等，与验证码输入无关，完整列表见
`output/code_screen_real.html`。）

自动提交测试（同一次浏览器会话，未刷新页面）：

```
提交按钮点击前是否 disabled：False
已输入假验证码 000000，未点击提交按钮，等待 3 秒观察是否自动提交…
提交按钮此刻是否 disabled：False
输入后、点击前捕获到的相关请求：[
  ('POST', 'https://claude.ai/api/event_logging/v2/batch'),
  ('POST', 'https://browser-intake-us5-datadoghq.com/api/v2/rum?...')
]
当前 URL：https://claude.ai/login
点击提交按钮后捕获到的相关请求：[
  ('POST', 'https://a-cdn.claude.ai/fc/gt2/public_key/EEA5F558-D6AC-4C03-B678-AABF639EE69A'),
  ('POST', 'https://api.hcaptcha.com/getcaptcha/a8086506-2036-46f4-ae50-00d8be805efa'),
  ('POST', 'https://browser-intake-us5-datadoghq.com/api/v2/rum?...')
]
点击提交按钮后 URL：https://claude.ai/login
```

点击提交按钮后页面出现了 hCaptcha 拖拽验证弹窗（截图 `output/code_screen_after_submit.png`），
因为用的是假验证码，无法继续验证成功路径下的跳转行为——这部分留给 Task 7 用真实验证码验证。

### 相关截图

- `output/code_screen.png`：`fill_email` 后的中间态"已发送链接"界面（0 input）。
- `output/code_screen_real.png`：点击 "Enter verification code" 后的真正验证码输入界面（1 input）。
- `output/code_screen_filled.png`：输入假验证码 `000000` 后、点提交前。
- `output/code_screen_after_submit.png`：点击提交按钮后弹出的 hCaptcha 拖拽验证弹窗。
- `output/waiting_login.png`：一次 Cloudflare 等待超过 120 秒导致 `wait_login_form` 超时时的截图（空白页）。

### 相关 DOM 文件

- `output/code_screen.html`：中间态界面完整 DOM。
- `output/code_screen_real.html`：真正验证码输入界面完整 DOM。

（`output/` 已在 `.gitignore` 中，本仓库不提交这些二进制/HTML 产物；如需复核请重跑
`scripts/probe_code_screen.py` 自行生成，注意会真的创建一个新邮箱。）
