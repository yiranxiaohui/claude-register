# 浏览器自动化练习项目 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 搭建一个 Python + Playwright 练手项目，用三个由浅入深的独立脚本掌握「用脚本打开网页并自动操作页面」。

**Architecture:** uv 管理的 Python 项目，`scripts/` 下三个互不依赖、可独立运行的脚本，分别对应打开页面截图、表单填写验证、登录会话复用与等待。无框架、无抽象层 —— 每个脚本自成一体，短到一眼看完。

**Tech Stack:** Python 3.13（由 uv 下载管理）、Playwright 同步 API、Chromium。靶站 saucedemo.com。

## Global Constraints

- 项目根目录：`D:\Projects\claude-register`，git 分支 `main`，remote origin 为 `git@github.com:yiranxiaohui/claude-register.git`
- 系统 `python` 是 Windows 商店存根，**不可用**。所有 Python 操作必须走 `uv run`，且 `uv init` 必须带 `--python 3.13` 让 uv 下载独立解释器
- 三个脚本**互不依赖**，任意一个可单独运行（`03` 自行处理登录）
- 全部使用**中文注释**，解释「为什么这么写」而非仅「这行做了什么」；运行时向终端打印进度
- 超时统一 15 秒（`page.set_default_timeout(15_000)`），而非 Playwright 默认的 30 秒
- `headless=False`，让运行者看得见浏览器窗口
- **不做静默兜底**：除 `02` 的失败路径外，异常一律原样抛出，不包 `try/except`
- 演示凭据 `standard_user` / `secret_sauce`，由 saucedemo 首页公开提供
- **不写 pytest**。每个脚本自带的 `expect()` 断言 + 截图就是它的验证方式
- 定位器优先用**用户可见文字**（`get_by_placeholder` / `get_by_role` / `get_by_text`），而非 CSS class —— saucedemo 是 React 渲染，class 名不稳定
- 每个任务结束时提交；**不 push**（推送需用户单独确认，且 GitHub 上的仓库可能尚未创建）

---

### Task 1: 环境搭建

把 uv 项目立起来并装好 Playwright 与 Chromium。这个任务的交付物是「能 import playwright 且浏览器内核已就位」，后面三个脚本任务都依赖它。

**Files:**
- Create: `pyproject.toml`（由 `uv init` 生成）
- Create: `.python-version`（由 `uv init` 生成，内容为 `3.13`）
- Modify: `.gitignore`（已存在，确认内容）
- Create: `output/.gitkeep`

**Interfaces:**
- Consumes: 无
- Produces: 可用的 `uv run` 环境；`playwright.sync_api` 可导入；Chromium 已安装。后续所有任务都以 `uv run scripts/<name>.py` 方式运行脚本。

- [ ] **Step 1: 初始化 uv 项目**

在 `D:\Projects\claude-register` 下运行：

```
uv init --python 3.13 --no-workspace
```

`--python 3.13` 是关键：不加它 uv 可能去用系统那个不可用的商店存根。
`--no-workspace` 避免 uv 把本目录挂到上层某个 workspace 上。

若 uv 生成了示例文件 `main.py` 或 `hello.py`，删掉它 —— 本项目的入口在 `scripts/` 下。

- [ ] **Step 2: 确认 Python 可用**

Run: `uv run python --version`
Expected: 输出 `Python 3.13.x`（不是报错，也不是商店存根的空输出）

若这里失败，后面全都跑不通，先解决再往下。

- [ ] **Step 3: 安装 Playwright 库**

```
uv add playwright
```

- [ ] **Step 4: 安装 Chromium 浏览器内核**

```
uv run playwright install chromium
```

约 150MB，需要几分钟。Playwright 用的是自己管理的浏览器副本，不依赖系统里已装的 Chrome。

- [ ] **Step 5: 验证安装成功**

Run: `uv run python -c "from playwright.sync_api import sync_playwright, expect; print('playwright ok')"`
Expected: 输出 `playwright ok`，无 ImportError

- [ ] **Step 6: 建好输出目录并确认 .gitignore**

创建 `output/.gitkeep`（空文件），让目录能被 git 跟踪但内容不入库。

确认 `.gitignore` 已包含以下各行（该文件在设计阶段已创建，此处仅核对）：

```
output/
.auth/
.venv/
__pycache__/
*.pyc
```

若 `output/` 被忽略导致 `.gitkeep` 加不进去，改用 `git add -f output/.gitkeep`。

- [ ] **Step 7: 提交**

```bash
git add pyproject.toml .python-version uv.lock .gitignore
git add -f output/.gitkeep
git commit -m "chore: 初始化 uv 项目并安装 Playwright"
```

---

### Task 2: 脚本 01 —— 打开页面 + 截图

最小可运行的自动化脚本。目标是确认环境通、看懂 Playwright 的对象层次。

**Files:**
- Create: `scripts/01_open_page.py`

**Interfaces:**
- Consumes: Task 1 的 uv 环境
- Produces: `output/01_homepage.png`。本脚本不被其他脚本引用（三个脚本互相独立）。

- [ ] **Step 1: 写脚本**

创建 `scripts/01_open_page.py`：

```python
"""脚本 01：打开网页并截图 —— 浏览器自动化的最小可运行例子。

运行：uv run scripts/01_open_page.py
"""

from pathlib import Path

from playwright.sync_api import sync_playwright

URL = "https://www.saucedemo.com/"
# 用脚本文件的位置推算项目根目录，这样在任何工作目录下运行结果都一样
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"


def main() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)

    # sync_playwright() 管理的是 Playwright 的驱动进程。
    # 必须用 with：退出时它会关掉驱动，否则后台会残留 node 进程。
    with sync_playwright() as p:
        # headless=False 会弹出真实浏览器窗口，练手时能亲眼看到脚本在做什么。
        # 改成 True 就是无头模式，跑得更快，适合以后放进 CI。
        browser = p.chromium.launch(headless=False)

        # 一个 browser 下可以开多个 page。这里只需要一个。
        page = browser.new_page()

        # 默认超时是 30 秒，练手时等太久。15 秒足够 saucedemo 响应，
        # 又能让写错的定位器更快暴露出来。
        page.set_default_timeout(15_000)

        print(f"正在打开：{URL}")
        page.goto(URL)

        # goto 返回时页面已经加载完毕，不需要再 sleep。
        print(f"页面标题：{page.title()}")
        print(f"当前地址：{page.url}")

        shot = OUTPUT_DIR / "01_homepage.png"
        page.screenshot(path=shot)
        print(f"截图已保存：{shot}")

        browser.close()

    print("完成。")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 运行脚本**

Run: `uv run scripts/01_open_page.py`

Expected:
- 弹出一个 Chromium 窗口，显示 saucedemo 登录页，随后自动关闭
- 终端依次打印：`正在打开：...`、`页面标题：Swag Labs`、`当前地址：https://www.saucedemo.com/`、`截图已保存：...`、`完成。`

- [ ] **Step 3: 肉眼复核截图**

打开 `output/01_homepage.png`，确认是 saucedemo 的登录页（有 "Swag Labs" logo 和用户名/密码输入框），而不是空白页或错误页。

若截图空白：说明页面还没渲染完就截了图。在 `page.screenshot` 前加一行
`page.get_by_placeholder("Username").wait_for()` 再跑一次。

- [ ] **Step 4: 提交**

```bash
git add scripts/01_open_page.py
git commit -m "feat: 添加脚本 01 —— 打开页面并截图"
```

---

### Task 3: 脚本 02 —— 表单填写 + 提交 + 验证

在 01 的基础上加入交互与断言。**成功和失败两条路径都要验证** —— 只跑通顺路径的脚本会在出问题时静默假装成功。

**Files:**
- Create: `scripts/02_fill_form.py`

**Interfaces:**
- Consumes: Task 1 的 uv 环境
- Produces: `output/02_login_success.png`、`output/02_login_failed.png`。本脚本不被其他脚本引用。

- [ ] **Step 1: 写脚本**

创建 `scripts/02_fill_form.py`：

```python
"""脚本 02：自动填写表单、提交、验证结果。

成功和失败两条路径都要跑 —— 只验证顺利路径的自动化脚本，
一旦线上出问题会"静默假装成功"，这是最危险的情况。

运行：uv run scripts/02_fill_form.py
"""

from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

URL = "https://www.saucedemo.com/"
USERNAME = "standard_user"
PASSWORD = "secret_sauce"  # saucedemo 首页公开的演示密码，非真实凭据
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"


def login(page: Page, username: str, password: str) -> None:
    """填写登录表单并提交。

    定位器优先用用户看得见的文字（占位符、按钮文字），而不是 CSS class。
    saucedemo 是 React 渲染的，class 名可能随构建变化，但界面上的
    "Username"、"Login" 这些字是稳定的。
    """
    page.goto(URL)
    page.get_by_placeholder("Username").fill(username)
    page.get_by_placeholder("Password").fill(password)
    page.get_by_role("button", name="Login").click()


def check_success(page: Page) -> None:
    """成功路径：正确凭据应当跳转到商品列表页。"""
    print("\n=== 成功路径 ===")
    login(page, USERNAME, PASSWORD)

    # expect() 会自动重试，直到条件成立或超时。
    # 这就是为什么不需要写 time.sleep(2) —— 页面快就立刻过，慢就多等一会。
    expect(page).to_have_url("https://www.saucedemo.com/inventory.html")

    # 光看 URL 不够：URL 对了但页面报错的情况是存在的。
    # 再确认一个真实商品渲染出来了，才算真的登录成功。
    expect(page.get_by_text("Sauce Labs Backpack")).to_be_visible()

    print(f"登录成功，已跳转到：{page.url}")

    shot = OUTPUT_DIR / "02_login_success.png"
    page.screenshot(path=shot)
    print(f"截图已保存：{shot}")


def check_failure(page: Page) -> None:
    """失败路径：错误密码应当留在登录页，并显示报错。

    注意这里的"错误"是我们主动预期的，所以要捕获并检查它。
    脚本其他地方的异常一律不包 try/except —— Playwright 的报错信息
    本身就写得很清楚，包起来反而把有用信息吃掉了。
    """
    print("\n=== 失败路径 ===")
    login(page, USERNAME, "wrong_password")

    # 错误提示以 "Epic sadface:" 开头，这是 saucedemo 的固定文案。
    error = page.locator("[data-test='error']")
    expect(error).to_be_visible()
    print(f"页面报错文案：{error.inner_text()}")

    # 没跳转，仍停在登录页 —— 这一条同样要验证。
    expect(page).to_have_url(URL)
    print(f"符合预期，仍停留在：{page.url}")

    shot = OUTPUT_DIR / "02_login_failed.png"
    page.screenshot(path=shot)
    print(f"截图已保存：{shot}")


def main() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        page.set_default_timeout(15_000)

        check_success(page)
        check_failure(page)

        browser.close()

    print("\n两条路径都通过。")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 运行脚本**

Run: `uv run scripts/02_fill_form.py`

Expected:
- 浏览器窗口中可见：输入框被自动填入、点击登录、跳转到商品页；随后第二轮用错误密码登录，页面顶部出现红色报错
- 终端打印 `=== 成功路径 ===`、`登录成功，已跳转到：https://www.saucedemo.com/inventory.html`、`=== 失败路径 ===`、`页面报错文案：Epic sadface: Username and password do not match any user in this service`、最后 `两条路径都通过。`

- [ ] **Step 3: 若定位器报错，按此排查**

如果报 `Timeout 15000ms exceeded waiting for locator(...)`，说明该定位器没匹配上。Playwright 的报错会写明它在等哪个选择器。逐个排查：

- `get_by_placeholder("Username")` 失败 → 用 `uv run playwright codegen https://www.saucedemo.com/` 打开录制器，手动点一下输入框，看它推荐什么定位器，照抄
- `[data-test='error']` 失败 → 改用文字匹配：`page.get_by_text("Epic sadface")`

改完重跑 Step 2，直到两条路径都通过。**不要**为了让脚本"跑过"而删掉断言。

- [ ] **Step 4: 肉眼复核两张截图**

`output/02_login_success.png` 应显示商品列表；`output/02_login_failed.png` 应显示登录页加红色错误提示。

- [ ] **Step 5: 提交**

```bash
git add scripts/02_fill_form.py
git commit -m "feat: 添加脚本 02 —— 表单填写与成功/失败双路径验证"
```

---

### Task 4: 脚本 03 —— 会话复用 + 等待

最进阶的一个。演示 `storage_state` 如何把登录状态存下来复用，以及 Playwright 的自动等待机制。

**Files:**
- Create: `scripts/03_session_reuse.py`

**Interfaces:**
- Consumes: Task 1 的 uv 环境
- Produces: `.auth/state.json`（登录状态，不入库）、`output/03_logged_in.png`、`output/03_cart.png`

- [ ] **Step 1: 写脚本**

创建 `scripts/03_session_reuse.py`：

```python
"""脚本 03：登录会话复用 + 等待机制。

第一次运行会真的登录一次，把 cookie 和 localStorage 存进 .auth/state.json；
之后再运行就直接带着这份状态开浏览器，跳过登录页。

运行：uv run scripts/03_session_reuse.py
（想重新走一遍登录流程，把 .auth/state.json 删掉再跑）
"""

from pathlib import Path

from playwright.sync_api import Browser, Page, expect, sync_playwright

URL = "https://www.saucedemo.com/"
INVENTORY_URL = "https://www.saucedemo.com/inventory.html"
USERNAME = "standard_user"
PASSWORD = "secret_sauce"  # saucedemo 首页公开的演示密码，非真实凭据

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"
AUTH_FILE = ROOT / ".auth" / "state.json"


def save_login_state(browser: Browser) -> None:
    """登录一次，把浏览器状态存成文件。

    context 是浏览器里一个隔离的"身份空间"，各自有独立的 cookie 和
    localStorage。storage_state() 就是把这个空间的内容导出来。
    """
    print("没有找到已保存的登录状态，先登录一次……")

    context = browser.new_context()
    context.set_default_timeout(15_000)
    page = context.new_page()

    page.goto(URL)
    page.get_by_placeholder("Username").fill(USERNAME)
    page.get_by_placeholder("Password").fill(PASSWORD)
    page.get_by_role("button", name="Login").click()
    expect(page).to_have_url(INVENTORY_URL)

    AUTH_FILE.parent.mkdir(exist_ok=True)
    context.storage_state(path=AUTH_FILE)
    print(f"登录状态已保存：{AUTH_FILE}")

    context.close()


def use_saved_state(browser: Browser) -> Page:
    """带着已保存的状态开一个新 context，直接进入已登录页面。"""
    print("正在用已保存的状态打开浏览器……")

    # storage_state 参数就是复用的关键：新 context 一出生就带着那些 cookie。
    context = browser.new_context(storage_state=AUTH_FILE)
    context.set_default_timeout(15_000)
    page = context.new_page()

    # 直接访问需要登录才能看的页面。如果状态没生效，saucedemo 会把我们
    # 踢回登录页 —— 下面这条断言就是在验证"复用真的成功了"。
    page.goto(INVENTORY_URL)
    expect(page).to_have_url(INVENTORY_URL)
    expect(page.get_by_text("Sauce Labs Backpack")).to_be_visible()

    print("跳过登录页，直接进入了商品列表")
    page.screenshot(path=OUTPUT_DIR / "03_logged_in.png")

    return page


def demo_waiting(page: Page) -> None:
    """演示自动等待：两个会改变页面的操作，都不用 sleep。"""
    print("\n=== 等待机制演示 ===")

    # 操作一：切换排序。选完之后整个商品列表会重新渲染。
    page.get_by_role("combobox").select_option("lohi")  # 价格从低到高
    # 排序生效后第一个商品会变成最便宜的那个。
    # expect 会自动重试到列表刷新完，不需要我们猜"要等几秒"。
    first_item = page.locator("[data-test='inventory-item-name']").first
    expect(first_item).to_have_text("Sauce Labs Onesie")
    print(f"已按价格升序排列，第一个商品：{first_item.inner_text()}")

    # 操作二：加购物车。角标数字是异步更新的。
    page.get_by_role("button", name="Add to cart").first.click()
    badge = page.locator(".shopping_cart_badge")
    expect(badge).to_have_text("1")
    print(f"已加入购物车，角标显示：{badge.inner_text()}")

    # 对比一下：如果这里写 time.sleep(2)，页面快的时候白等 2 秒，
    # 网络慢的时候又不够用，脚本会时灵时不灵。expect 两个问题都没有。

    page.screenshot(path=OUTPUT_DIR / "03_cart.png")
    print(f"截图已保存：{OUTPUT_DIR / '03_cart.png'}")


def main() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)

        if not AUTH_FILE.exists():
            save_login_state(browser)
        else:
            print(f"找到已保存的登录状态：{AUTH_FILE}")

        page = use_saved_state(browser)
        demo_waiting(page)

        browser.close()

    print("\n完成。再跑一次会直接复用状态，跳过登录。")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 首次运行（无状态文件，走登录）**

先确保 `.auth/state.json` 不存在，然后：

Run: `uv run scripts/03_session_reuse.py`

Expected: 终端打印 `没有找到已保存的登录状态，先登录一次……` → `登录状态已保存：...` → `正在用已保存的状态打开浏览器……` → `跳过登录页，直接进入了商品列表` → 排序和购物车两条 → `完成。`

- [ ] **Step 3: 二次运行（验证复用真的生效）**

Run: `uv run scripts/03_session_reuse.py`

Expected: 这次第一行变成 `找到已保存的登录状态：...`，**不再出现登录相关输出**，浏览器窗口里也不会闪过登录页 —— 直接就是商品列表。

这一步是本任务的核心验证：如果二次运行仍然走了登录，说明 `storage_state` 没起作用。

- [ ] **Step 4: 若定位器或断言报错，按此排查**

- `get_by_role("combobox")` 失败 → 改用 `page.locator("[data-test='product-sort-container']")`
- `expect(first_item).to_have_text("Sauce Labs Onesie")` 失败 → 报错信息会打印实际文本。若 saucedemo 改了商品价格导致最便宜的不是它了，把断言改成实际那个商品名
- `.shopping_cart_badge` 失败 → 用 `uv run playwright codegen https://www.saucedemo.com/` 登录后点一下购物车图标，看录制器给出的定位器

- [ ] **Step 5: 确认状态文件没被 git 跟踪**

Run: `git status --short`
Expected: 输出中**不含** `.auth/state.json`。该文件是登录后的 session cookie，虽然是演示账号，但 session 文件不入库这个习惯要从第一天建立。

若它出现了，说明 `.gitignore` 里的 `.auth/` 那行有问题，修好再往下。

- [ ] **Step 6: 提交**

```bash
git add scripts/03_session_reuse.py
git commit -m "feat: 添加脚本 03 —— 登录会话复用与自动等待"
```

---

### Task 5: README 与收尾

补上文档，让这个项目过几周回来看还能用起来。

**Files:**
- Create: `README.md`

**Interfaces:**
- Consumes: 前四个任务的全部产物
- Produces: 无（终点任务）

- [ ] **Step 1: 写 README.md**

创建 `README.md`，包含四部分。以下是完整内容，直接用：

````markdown
# 浏览器自动化练习

用 Python + Playwright 练习「用脚本打开网页并自动操作页面」。
三个脚本由浅入深，互不依赖，可以单独跑任意一个。

靶站是 [saucedemo.com](https://www.saucedemo.com/) —— 专为自动化练习搭建的
电商演示站，凭据由站点首页公开提供，无验证码与风控。

## 环境搭建

```
uv init --python 3.13 --no-workspace
uv add playwright
uv run playwright install chromium
```

最后一步会下载约 150MB 的浏览器内核。Playwright 用自己管理的浏览器副本，
不依赖系统里装的 Chrome。

## 三个脚本

| 脚本 | 练什么 | 运行 |
|------|--------|------|
| `01_open_page.py` | 启动浏览器、打开页面、截图 | `uv run scripts/01_open_page.py` |
| `02_fill_form.py` | 填表单、提交、验证成功与失败两条路径 | `uv run scripts/02_fill_form.py` |
| `03_session_reuse.py` | 保存登录状态并复用、自动等待机制 | `uv run scripts/03_session_reuse.py` |

截图输出在 `output/`，登录状态存在 `.auth/`，两者都不入库。

## 常见问题

**报错 `Executable doesn't exist at ...`**
浏览器内核没装。跑 `uv run playwright install chromium`。

**想让浏览器不弹窗口**
把脚本里的 `headless=False` 改成 `True`。跑得更快，但看不到过程 ——
练手阶段建议保持 `False`。

**报错 `Timeout 15000ms exceeded waiting for locator(...)`**
定位器没匹配上。Playwright 的报错会写明它在等哪个选择器。
用 `uv run playwright codegen https://www.saucedemo.com/` 打开录制器，
手动操作一遍，照抄它推荐的定位器。

**靶站访问不了**
saucedemo 是国外站点。确认网络能通：
`curl -I https://www.saucedemo.com`。

**想重新走一遍 03 的登录流程**
删掉 `.auth/state.json` 再运行。

## 往下可以练什么

- **数据抓取**：把商品列表的名称和价格提取出来，存成 CSV 或 JSON
- **无头模式**：全部改成 `headless=True`，体会速度差别
- **多浏览器**：把 `p.chromium` 换成 `p.firefox` 或 `p.webkit`
- **调试技巧**：用 `PWDEBUG=1 uv run scripts/02_fill_form.py` 打开
  Playwright Inspector，一步步单步执行
- **重构成 CLI**：把三个脚本合并成一个带子命令的工具，公共逻辑抽成模块
- **接入 pytest**：用 `pytest-playwright` 插件把这些改写成真正的端到端测试
````

- [ ] **Step 2: 从零验证一遍 README 的准确性**

按 README 里写的命令逐条核对：表格里三条运行命令都能跑通，`headless` 那一段提到的变量名在三个脚本里都真实存在。

Run: `uv run scripts/01_open_page.py`
Expected: 正常完成（这是对 README 表格第一行的抽查）

- [ ] **Step 3: 确认工作区干净**

Run: `git status --short`
Expected: 除 `README.md` 外无未跟踪的意外文件；`.auth/state.json`、`output/*.png`、`.venv/` 均不出现

- [ ] **Step 4: 提交**

```bash
git add README.md
git commit -m "docs: 添加 README"
```

- [ ] **Step 5: 汇报实际运行结果，并询问是否 push**

把三个脚本的真实终端输出贴给用户 —— 不能只说「写好了」。

然后询问是否要 push 到 `git@github.com:yiranxiaohui/claude-register.git`。
**不要自行 push**：推送是对外操作，且该仓库在 GitHub 上可能尚未创建
（若未创建，需用户先建好，或用 `gh repo create` 但同样要先问过）。

---

## 自审记录

- **Spec 覆盖**：目录结构 → Task 1；三个脚本 → Task 2/3/4；错误处理约定 → 写入 Global Constraints 并体现在各脚本代码中；验证方式（断言+截图、实际运行后汇报）→ 各任务的运行步骤 + Task 5 Step 5；README 四部分 → Task 5 Step 1；.gitignore → Task 1 Step 6 + Task 4 Step 5 复核
- **占位符**：无 TBD/TODO，所有代码块为完整可运行内容
- **命名一致性**：`OUTPUT_DIR`、`AUTH_FILE`、`login()`、`INVENTORY_URL` 在跨任务引用处拼写一致；三个脚本互不 import，无跨文件签名依赖
- **已知风险**：saucedemo 由 React 渲染，原始 HTML 中拿不到选择器，故计划中的定位器以用户可见文字为主，并在 Task 3 Step 3 与 Task 4 Step 4 提供了 codegen 排查路径
