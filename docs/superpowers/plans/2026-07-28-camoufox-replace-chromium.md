# Camoufox 替换 Chromium 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把浏览器引擎从 Chromium 换成 Camoufox（Firefox 系隐身浏览器），以稳定通过 claude.ai 前的 Cloudflare 挑战。

**Architecture:** 改动集中在浏览器启动层。在 `browser.py` 新增一个 `browser_session()` 上下文管理器封装 Camoufox 配置（虚拟显示 + humanize + locale + geoip），删掉旧的 `launch_browser`；`flow.py` 里把 `sync_playwright()` + `launch_browser(p)` 换成 `with browser_session() as browser:`。其余页面交互函数只吃通用 Playwright `Page`，全部不动。

**Tech Stack:** Python 3.13、uv、Camoufox（`camoufox[geoip]`，基于 Playwright/Firefox）、Xvfb（虚拟显示，容器已装）、pytest。

参考 spec：`docs/superpowers/specs/2026-07-28-camoufox-replace-chromium-design.md`

---

## 文件结构

| 文件 | 责任 | 本计划动作 |
|------|------|-----------|
| `pyproject.toml` | 依赖声明 | 加 `camoufox[geoip]` |
| `claude_register/browser.py` | 浏览器启动 + 页面交互 | 新增 `browser_session()`，删 `launch_browser`，调整 `new_page` |
| `claude_register/flow.py` | 编排 | 用 `browser_session()` 替换 `sync_playwright()`+`launch_browser` |
| `README.md` | 安装/使用说明 | 安装步骤改 `camoufox fetch`，补虚拟显示取舍 |
| `tests/` | 单测 | 不改（不覆盖浏览器启动层），仅回归 |

---

## Task 1: 加 Camoufox 依赖并 fetch 浏览器

**Files:**
- Modify: `pyproject.toml`（`dependencies` 数组）

- [ ] **Step 1: 加依赖**

修改 `pyproject.toml` 的 `dependencies`，在 `playwright` 之后加一行：

```toml
dependencies = [
    "httpx>=0.28.1",
    "playwright>=1.61.0",
    "camoufox[geoip]>=0.4.11",
]
```

- [ ] **Step 2: 同步依赖**

Run: `uv sync`
Expected: 安装成功，输出含 `camoufox`；无报错。

- [ ] **Step 3: 下载 Camoufox 浏览器二进制**

Run: `uv run camoufox fetch`
Expected: 下载 Firefox 二进制（约 100–150MB）到 `~/.cache/camoufox`，结束打印完成信息。

若报网络错误可重试；这是一次性运行时资源下载（类比 `playwright install`），不是构建。

- [ ] **Step 4: 验证 Camoufox 可导入**

Run: `uv run python -c "from camoufox.sync_api import Camoufox; print('ok')"`
Expected: 打印 `ok`。

- [ ] **Step 5: 提交**

```bash
git add pyproject.toml uv.lock
git commit -m "build: 加入 camoufox[geoip] 依赖"
```

---

## Task 2: 在 browser.py 里新增 browser_session，删除 launch_browser

**Files:**
- Modify: `claude_register/browser.py`

本任务无单测（延续 browser.py 不写 mock 单测的既定原则）。验证靠「能导入 + 能实际启动一个 Camoufox 会话并打开一张空白页」这个冒烟脚本。

- [ ] **Step 1: 改 import**

在 `claude_register/browser.py` 顶部，把：

```python
from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import Page, expect
```

改为：

```python
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

from camoufox.sync_api import Camoufox
from playwright.sync_api import Page, expect
```

- [ ] **Step 2: 用 browser_session 替换 launch_browser**

删除现有的 `launch_browser` 函数（第 24–35 行整段）：

```python
def launch_browser(p):
    common = {
        "headless": False,
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    try:
        browser = p.chromium.launch(channel="chrome", **common)
        log("已启动本机 Chrome（channel=chrome）")
        return browser
    except Exception as exc:
        log(f"本机 Chrome 不可用（{exc}），回退到 Playwright Chromium")
        return p.chromium.launch(**common)
```

在原位置替换为：

```python
@contextmanager
def browser_session():
    """启动 Camoufox（Firefox 系隐身浏览器）会话。

    headless="virtual" 自动包 Xvfb，适配无显示的容器，且比真 headless 更抗
    Cloudflare 检测；humanize 提供人性化光标移动；locale/geoip 让指纹统一。
    """
    try:
        cm = Camoufox(
            headless="virtual",
            humanize=True,
            locale="en-US",
            geoip=True,
        )
    except Exception as exc:
        raise RuntimeError(
            f"启动 Camoufox 失败（{exc}）。请先运行 `uv run camoufox fetch` "
            "下载浏览器二进制，并确认已安装 Xvfb。"
        ) from exc
    with cm as browser:
        log("已启动 Camoufox（headless=virtual）")
        yield browser
```

- [ ] **Step 3: 调整 new_page，把 locale 上移**

把现有 `new_page`（原第 38–45 行）：

```python
def new_page(browser):
    context = browser.new_context(
        locale="en-US",
        viewport={"width": 1280, "height": 900},
    )
    page = context.new_page()
    page.set_default_timeout(30_000)
    return context, page
```

改为（移除 context 层的 `locale`，避免与 Camoufox 指纹层的 locale 冲突；viewport 保留）：

```python
def new_page(browser):
    context = browser.new_context(
        viewport={"width": 1280, "height": 900},
    )
    page = context.new_page()
    page.set_default_timeout(30_000)
    return context, page
```

- [ ] **Step 4: 冒烟验证——能启动并打开空白页**

Run:
```bash
uv run python -c "
from claude_register.browser import browser_session, new_page
with browser_session() as b:
    ctx, page = new_page(b)
    page.goto('about:blank')
    print('OK', page.url)
    ctx.close()
"
```
Expected: 打印 `已启动 Camoufox（headless=virtual）` 和 `OK about:blank`，无异常退出。

若报 Xvfb 相关错误，确认 `which Xvfb` 有输出（本容器已装）。

- [ ] **Step 5: 提交**

```bash
git add claude_register/browser.py
git commit -m "feat: 用 Camoufox 会话替换 Chromium 启动逻辑"
```

---

## Task 3: flow.py 改用 browser_session

**Files:**
- Modify: `claude_register/flow.py`

- [ ] **Step 1: 改 import**

把 `claude_register/flow.py` 顶部：

```python
from playwright.sync_api import sync_playwright

from claude_register.anymail import AnyMailClient, Mailbox, load_dotenv
from claude_register.browser import (
    fill_code,
    fill_email,
    hcaptcha_visible,
    launch_browser,
    new_page,
    open_login,
    open_magic_link,
    pause_for_user,
    screenshot,
    wait_code_screen,
    wait_login_form,
)
```

改为（删掉 `sync_playwright` 导入，把 `launch_browser` 换成 `browser_session`）：

```python
from claude_register.anymail import AnyMailClient, Mailbox, load_dotenv
from claude_register.browser import (
    browser_session,
    fill_code,
    fill_email,
    hcaptcha_visible,
    new_page,
    open_login,
    open_magic_link,
    pause_for_user,
    screenshot,
    wait_code_screen,
    wait_login_form,
)
```

- [ ] **Step 2: 改 run_browser 的启动块**

把 `run_browser` 里（原第 58–59 行）：

```python
    with sync_playwright() as p:
        browser = launch_browser(p)
        context, page = new_page(browser)
```

改为：

```python
    with browser_session() as browser:
        context, page = new_page(browser)
```

`finally` 里的 `context.close()` / `browser.close()` 保留不变（`browser_session` 的 `with` 退出会再收尾一次，Camoufox/Playwright 的 close 幂等，重复关闭无害）。

- [ ] **Step 3: 验证 import 无环、无语法错**

Run: `uv run python -c "import claude_register.flow; print('ok')"`
Expected: 打印 `ok`，无 ImportError / 语法错误。

- [ ] **Step 4: 全量回归单测**

Run: `uv run pytest tests/ -q`
Expected: `62 passed`（浏览器启动层不被单测覆盖，改动不影响既有测试）。

- [ ] **Step 5: 提交**

```bash
git add claude_register/flow.py
git commit -m "refactor: flow 改用 browser_session 启动 Camoufox"
```

---

## Task 4: 更新 README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 改安装步骤**

把 README「准备」一节的代码块（第 10–13 行）：

```text
uv sync
uv run playwright install chromium
```

改为：

```text
uv sync
uv run camoufox fetch
```

- [ ] **Step 2: 补虚拟显示取舍说明**

在「已知的坑」一节的 **Cloudflare** 段落之前，新增一段：

```markdown
**浏览器引擎是 Camoufox（Firefox 系隐身浏览器），不是 Chromium。** 它以
`headless="virtual"` 模式运行——自动包一层 Xvfb 虚拟显示，既适配无图形界面的
服务器/容器，也比真 headless 更能扛住 Cloudflare 挑战。运行前需装好 Xvfb，并跑过
一次 `uv run camoufox fetch` 下载浏览器二进制。

**虚拟显示下你看不到浏览器实时画面。** 关键步骤会截图到 `output/`。默认的魔术链接
路径全程无需人工实时交互，不受影响；但如果走到验证码那条路弹出了 hCaptcha 拖拽题，
在没有图形界面的机器上就没法手动拖拽——需要换到带显示的环境，或接 VNC。
```

- [ ] **Step 3: 校对措辞与既有文风一致**

Run: `uv run python -c "print(open('README.md').read()[:200])"`
Expected: 正常打印开头，确认文件未损坏。人工快速通读改动两段，确认与全篇口吻一致。

- [ ] **Step 4: 提交**

```bash
git add README.md
git commit -m "docs: README 安装步骤改 camoufox fetch，补虚拟显示取舍"
```

---

## Task 5: 端到端实跑验收（本计划的验收点）

**Files:** 无改动，仅运行。

此步依赖 claude.ai 当时的 Cloudflare 状态与有效的 AnyMail 凭证（`.env`），可能需重试，无法保证一次成功。这是本改动唯一的真实价值点。

- [ ] **Step 1: 确认 .env 就绪**

确认 `.env` 存在且填了 AnyMail 配置（`ANYMAIL_API_KEY` 等）。若没有则本步无法进行，如实记录为「凭证缺失，未能验收」并停下。

- [ ] **Step 2: 跑一次完整流程**

Run: `uv run main.py -d <一个可用后缀> --login-timeout 180`
（或用 `-e <已有邮箱>` 复用邮箱。）

观察日志：
- 是否打印「已启动 Camoufox（headless=virtual）」
- `wait_login_form` 是否在超时内等到登录表单（= 过了 Cloudflare），还是仍卡在挑战页/空白页
- 若拿到表单：邮箱是否填入、是否收到魔术链接、`open_magic_link` 后 `page.url` 是否跳转到已登录态

- [ ] **Step 3: 收集证据**

查看 `output/` 下截图（`after_magic_link.png` / `waiting_login.png` 等），据此判断：
- **成功**：过盾并走到魔术链接打开——如实记录，端到端闭环达成。
- **仍失败**：记录卡在哪一步（Cloudflare 超时？空白页？），保留截图。这不代表改动有误，可能是 claude.ai 当时状态；换时间重试或记入风险清单。

- [ ] **Step 4: 如实记录结论**

不论成败，把实跑结论写进 `docs/superpowers/notes/`（新建一份 `2026-07-28-camoufox-e2e-result.md`），包含：跑的命令、关键日志、截图文件名、结论。

- [ ] **Step 5: 提交笔记**

```bash
git add docs/superpowers/notes/2026-07-28-camoufox-e2e-result.md
git commit -m "docs: 记录 Camoufox 端到端实跑结论"
```

---

## 风险与注意

- **Camoufox fetch 需要外网**（github release）。容器若走代理，确认代理生效。
- **首次启动较慢**：virtual 模式要拉起 Xvfb，比直接 chromium 慢几秒，属正常。
- **DOM 选择器**：`fill_email`/`_code_input` 等选择器按真实 claude.ai DOM 抓取，Firefox 不改变站点 DOM，理论上通用；若实跑发现某选择器在 Firefox 下失效，记入笔记再单独修，不在本计划范围内预先改。
- **幂等关闭**：`run_browser` 的 `finally` 与 `browser_session` 的 `with` 退出会各关一次；Playwright close 幂等，无害。若实跑报重复关闭异常（不预期），可把 `finally` 里的 `browser.close()` 去掉，交给 `browser_session` 收尾。
