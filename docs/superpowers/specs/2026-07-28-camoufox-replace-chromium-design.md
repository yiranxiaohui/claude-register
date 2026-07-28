# 设计：用 Camoufox 替换 Chromium

**日期**：2026-07-28
**状态**：已批准，待写实现计划

## 背景与目标

claude.ai 登录页前有 Cloudflare 挑战，当前用 Chromium（`channel=chrome`，回退 Playwright Chromium）实测长期卡在挑战页或放行后空白页，一次完整登录流程从未跑通。

目标：把浏览器引擎换成 **Camoufox**（Firefox 系隐身浏览器，自带指纹轮换与人性化交互），以求稳定通过 Cloudflare。改动集中在浏览器启动这一层，页面交互函数保持不变。

**决策（已确认）**：
1. **全替换** Chromium，不保留双引擎开关——Chromium 在唯一重要的任务（过盾）上已证明失败，删掉它让 `run_browser` 保持单一路径。
2. **精调默认配置**：虚拟显示 + `humanize` + `locale`，其余交给 Camoufox 默认指纹轮换。

## 环境事实（已核实）

- 本容器（LXC 1006 happy）**无 DISPLAY**，但已装 `Xvfb` / `xvfb-run`。
- 当前只装了 Playwright Chromium，未装 Camoufox。
- Python 3.13.5，uv 0.11.20。

## 架构与改动

改动本质：把
```
with sync_playwright() as p:
    browser = launch_browser(p)   # chromium.launch(channel="chrome", ...)
```
换成 Camoufox 的上下文管理器。为避免引擎细节泄进 `flow.py`，在 `browser.py` 内封装一个上下文管理器。

### `browser.py`

- **新增** `browser_session()` 上下文管理器，封装 Camoufox 配置：
  ```python
  from contextlib import contextmanager
  from camoufox.sync_api import Camoufox

  @contextmanager
  def browser_session():
      with Camoufox(
          headless="virtual",   # 自动包 Xvfb，适配无显示容器，且比真 headless 更抗检测
          humanize=True,        # 人性化光标移动
          locale="en-US",       # 与页面语言一致，指纹统一
          geoip=True,           # 依赖 camoufox[geoip]
      ) as browser:
          yield browser
  ```
- **删除** `launch_browser(p)`：其中的 chromium-only 参数（`--disable-blink-features=AutomationControlled`）和 `channel="chrome"` 回退逻辑全部移除。
- **调整** `new_page()`：只设 `viewport`，`locale` 上移到 Camoufox 层保证指纹一致（避免 context 层与浏览器指纹层的 locale 冲突）。
- **不动**：`fill_email`、`wait_login_form`、`open_login`、`open_magic_link`、`wait_code_screen`、`fill_code`、`_code_input`、`_reveal_code_input`、`hcaptcha_visible`、`_submit_code`、`screenshot`、`pause_for_user`。这些只吃通用 `Page`，选择器按真实 claude.ai DOM 抓取，Firefox 不改变其 DOM。

### `flow.py`

- `run_browser` 里 `with sync_playwright() as p: browser = launch_browser(p)` 改为 `with browser_session() as browser:`。
- 删除 `from playwright.sync_api import sync_playwright` 导入；从 `browser` 导入改为引入 `browser_session`，去掉 `launch_browser`。
- 其余编排逻辑（等表单、填邮箱、轮询魔术链接/验证码、兜底、pause）保持不变。

### 依赖与资源

- `pyproject.toml` 依赖加 `camoufox[geoip]`（保留 `playwright`，Camoufox 依赖它）。
- 安装流程：`uv sync` 后执行 `camoufox fetch` 下载 Firefox 二进制（类比 `playwright install`，属运行时资源下载，非构建，允许本地执行）。

## 错误处理

- Camoufox 启动失败（二进制未 fetch、Xvfb 缺失）应给出可读报错提示（提示先 `camoufox fetch`），而非裸异常冒泡。
- 虚拟显示下无法实时观看浏览器：`pause_for_user()` 与手动拖拽 hCaptcha 这条路在本容器里退化为只能看 `output/` 截图。默认魔术链接路径无需实时交互，不受影响。README 注明此取舍。

## 测试

- 62 个既有单测都不触及浏览器启动层，改完应原样通过——回归验证即可。
- 延续 `browser.py` 不写 mock 单测的既定原则，不新增单测。
- **验收 = 端到端实跑一次**：观察能否真正过 Cloudflare 拿到登录表单并走到魔术链接。此步依赖 claude.ai 当时状态与 AnyMail 凭证，可能需重试，无法保证一次成功——但这是本改动唯一的真实价值点。

## 文档

- README：把 `uv run playwright install chromium` 改为 `camoufox fetch`；新增虚拟显示/截图取舍说明。
- `.env.example`：如涉及新配置项则同步（预计无新增必填项）。

## 非目标（YAGNI）

- 不做 `--browser` 引擎切换开关。
- 不尝试自动绕过 hCaptcha（维持只检测不绕过）。
- 不把 headless 模式做成可配置项——统一虚拟显示。
