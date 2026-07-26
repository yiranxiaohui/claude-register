# 浏览器自动化练习项目 — 设计文档

日期：2026-07-26
状态：已批准，待实现

## 目标

搭建一个用于**学习浏览器自动化**的练手项目。核心能力是「用脚本打开网页并自动操作页面」，通过三个由浅入深的独立脚本掌握：打开页面与截图、表单填写与提交验证、登录会话复用与等待机制。

明确的非目标：

- 不做账号自动注册、不绕过验证码或任何验证机制
- 不抓取数据存文件（本轮不涉及）
- 不做 CLI 工具封装（可作为练熟之后的第二阶段）

## 技术选型

**Python + Playwright**，用 `uv` 管理项目与依赖。

选型理由：

- Playwright 自带等待机制，`fill` / `click` 前会自动等元素可交互，不必手写 `sleep`，入门阻力远小于 Selenium
- Python 脚本短、可读性好，适合「一眼看完一个知识点」的练手场景
- 机器上无真实 Python（仅有 Windows 商店存根），但 `uv` 可自动下载并管理独立 Python，绕开存根问题

环境实测（2026-07-26）：Node.js v26.0.0、npm 11.12.1、uv 0.11.14、git 均可用；`python` 为商店存根不可用。

## 靶站

统一使用 **saucedemo.com** —— 专为自动化练习搭建的电商演示站，具备登录页、商品列表、购物车，三个脚本可串成一条连贯的线。

- 演示凭据 `standard_user` / `secret_sauce` 由站点首页公开提供，不涉及任何真实账户
- 页面结构稳定，无验证码与风控
- 连通性已实测通过

不使用 claude.ai 等真实站点的注册/登录表单作为练习目标：会遭遇验证码与风控，既学不到自动化本身，也违反站点使用条款。

## 目录结构

```
D:\Projects\claude-register\
├─ pyproject.toml          # uv 项目定义与依赖
├─ .python-version         # 锁定 Python 版本（3.13）
├─ .gitignore
├─ README.md
├─ scripts/
│  ├─ 01_open_page.py
│  ├─ 02_fill_form.py
│  └─ 03_session_reuse.py
├─ output/                 # 截图输出（不入库）
├─ .auth/                  # 登录状态文件（不入库）
└─ docs/superpowers/specs/ # 设计文档
```

Git：仓库已初始化，remote origin 为 `git@github.com:yiranxiaohui/claude-register.git`，默认分支改为 `main`（与 GitHub 默认一致）。

## 环境搭建

```
uv init --python 3.13
uv add playwright
uv run playwright install chromium
```

`--python 3.13` 让 uv 自动下载独立 Python，绕开系统上不可用的商店存根。后续运行一律通过 `uv run scripts/<name>.py`，无需手动激活虚拟环境。

## 三个脚本

三者**互不依赖，均可独立运行**（`03` 自行处理登录）。全部使用中文注释，解释「为什么这么写」而非仅「这行做了什么」；运行时向终端打印进度。

### 01_open_page.py — 打开页面 + 截图

流程：启动 Chromium（`headless=False`，可见浏览器窗口）→ 打开 saucedemo 首页 → 打印页面标题与 URL → 截图至 `output/01_homepage.png` → 关闭。

知识点：`playwright → browser → page` 三层对象关系；`headless` 开关的差异；`with` 语句自动释放资源。

### 02_fill_form.py — 填表 + 提交 + 验证

流程分两条路径：

- **成功路径**：打开登录页 → 填入用户名密码 → 点击登录 → 断言跳转至 `/inventory.html` 且商品列表可见 → 截图
- **失败路径**：故意填入错误密码 → 捕获并打印页面报错文案

知识点：`get_by_placeholder` / `get_by_role` 等定位器的取舍；`fill()` 与 `click()`；用 `expect()` 断言而非 `sleep`；成功与失败两条路径都需验证。

保留失败路径的理由：只验证顺利路径的自动化脚本，在线上出问题时会「静默假装成功」，这是入门阶段最该建立的意识。

### 03_session_reuse.py — 会话复用 + 等待

流程分两段：

- **保存**：若 `.auth/state.json` 不存在，登录一次并通过 `context.storage_state()` 保存 cookie 与 localStorage
- **复用**：新建 context 时传入该 state 文件，直接进入已登录页面，跳过登录；随后执行需等待的操作 —— 切换商品排序（触发 DOM 更新）、加入购物车、等待购物车角标数字变化

知识点：`storage_state` 的存与用；`browser.new_context()` 的作用；自动等待机制；`expect(locator).to_have_text()` 相较 `time.sleep(2)` 为何更可靠。

## 错误处理

原则：**出错要看得懂**，而非出错要撑住。

- 不做静默兜底。网络超时、元素找不到、断言失败一律让异常原样抛出 —— Playwright 的报错本身已说明等待的选择器、超时时长与页面状态，包装成 `try/except` 反而会吞掉这些信息
- 唯一例外是 `02` 的失败路径：那是主动预期的错误，需捕获并打印页面报错文案，因为「验证错误提示是否正确显示」正是该段脚本的目的
- 超时统一设为 15 秒（Playwright 默认 30 秒，练手时等待过久；15 秒足够 saucedemo 响应）

## 验证方式

不编写单元测试。三个练手脚本的正确性完全体现于「运行后页面是否按预期反应」，为其编写 pytest 属于为测试而测试。

验证依靠每个脚本自带的断言与截图：`02` 断言登录后的 URL 与商品列表，`03` 断言购物车角标数字；断言失败则脚本直接报错退出。截图落在 `output/` 供肉眼复核。

实现完成后需逐个实际运行，并将真实输出反馈给用户，不得仅声称「已写好」。

## README.md

包含四部分：

1. 环境搭建的三条 uv 命令
2. 每个脚本的一句话说明与运行命令
3. 常见问题：浏览器未安装、`headless` 如何调整、靶站无法访问时怎么办
4. 后续可继续练习的方向

## .gitignore

排除 `output/`、`.auth/`、`.venv/`、`__pycache__/`。

`.auth/` 必须排除：其中是登录后的 session cookie。虽为演示账号，但「session 文件不入库」的习惯值得从第一天建立。
