# Camoufox 端到端实跑结论

**日期**：2026-07-28
**分支**：`worktree-camoufox-replace-chromium`
**对应计划**：`docs/superpowers/plans/2026-07-28-camoufox-replace-chromium.md`（Task 5）

## 结论一句话

**Camoufox 成功过掉 claude.ai 前的 Cloudflare 挑战，登录表单正常出现**——这正是原来 Chromium 连续几小时做不到的事。核心价值已验证。完整邮件闭环（收魔术链接并打开）因本环境无 AnyMail 凭证（`.env` 缺失）未能实跑。

## 环境

- LXC 1006 happy，无 DISPLAY，已装 Xvfb（`/usr/bin/Xvfb`）。
- Camoufox 0.4.11，二进制 v152.0.4-beta.28，`headless="virtual"`。

## 跑了什么

无 `.env`（AnyMail 凭证缺失，`.gitignore` 忽略、主检出与 worktree 均无），因此没跑 `main.py` 全流程。改为跑一段探针，直接调用真实浏览器函数验证 Cloudflare 过盾这一关键环节：

```python
from claude_register.browser import browser_session, new_page, open_login, wait_login_form, screenshot
with browser_session() as b:
    ctx, page = new_page(b)
    open_login(page)
    wait_login_form(page, timeout_ms=120_000)   # 等到邮箱输入框 = 过了 Cloudflare
    screenshot(page, 'e2e_probe.png')
    ctx.close()
```

## 关键日志

```
已启动 Camoufox（headless=virtual）
正在打开：https://claude.ai/login
页面标题：Sign in - Claude
当前地址：https://claude.ai/login
登录表单已出现。
RESULT: PAST_CLOUDFLARE_FORM_VISIBLE
FINAL_URL https://claude.ai/login | TITLE Sign in - Claude
```

## 截图

`output/e2e_probe.png`：完整渲染的真实 claude.ai 登录页——「Continue with Google」、「Enter your email」输入框、「Continue with email」按钮，下方还有完整的 Explore plans / FAQ。既不是 Cloudflare 挑战页，也不是放行后的空白页。

## 已验证 vs. 未验证

- ✅ **已验证**：Camoufox 在无显示容器里以虚拟显示启动；打开 claude.ai/login；过 Cloudflare；登录表单（邮箱框）出现。
- ⚠️ **未验证（需 AnyMail 凭证）**：填邮箱 → 收魔术登录链接 → `open_magic_link` 打开完成登录这一后半段。逻辑未改，但没有真实凭证跑一遍。补上 `.env` 后可用 `uv run main.py -d <后缀> --login-timeout 180` 完整验收。

## 实现期发现的偏差（已并入代码）

计划里 `new_page` 保留了 Playwright 的 `viewport={"width":1280,"height":900}`，但 **Camoufox/Firefox 不接受 context 层的 `viewport` 参数**（会抛 `Browser.setDefaultViewport` 协议错误 `<root>.viewport.isMobile ... not described in this scheme`）。改法：窗口尺寸移到 `Camoufox(window=(1280, 900))`，`new_context` 用 `no_viewport=True`。实测窗口内尺寸 1280x847（高度扣掉 Firefox chrome），符合预期。见提交 `17f4b19`。
