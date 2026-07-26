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

**Git Bash 里跑脚本，中文 print 输出变成乱码**
这是终端默认代码页的显示问题，脚本本身跑得好好的，不影响功能。
运行前设置 `PYTHONIOENCODING=utf-8`（如 `PYTHONIOENCODING=utf-8 uv run scripts/01_open_page.py`），
或者干脆换成 PowerShell 运行。

## 往下可以练什么

- **数据抓取**：把商品列表的名称和价格提取出来，存成 CSV 或 JSON
- **无头模式**：全部改成 `headless=True`，体会速度差别
- **多浏览器**：把 `p.chromium` 换成 `p.firefox` 或 `p.webkit`
- **调试技巧**：用 `PWDEBUG=1 uv run scripts/02_fill_form.py` 打开
  Playwright Inspector，一步步单步执行
- **重构成 CLI**：把三个脚本合并成一个带子命令的工具，公共逻辑抽成模块
- **接入 pytest**：用 `pytest-playwright` 插件把这些改写成真正的端到端测试
