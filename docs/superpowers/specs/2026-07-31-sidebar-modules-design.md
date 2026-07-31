# 管理面板：侧边栏布局 + 模块拆分 设计

日期：2026-07-31
状态：已确认（方案 B）

## 目标

1. 把 3x-ui 节点配置从设置页拆出，成为独立导航模块。
2. 整体布局从「顶部两 tab」改为左侧边栏管理后台布局，页面按职责拆分。
3. 视觉精细化：统一 design token，自定义控件，纯深色主题，零新依赖。

## 布局与路由

- `App.jsx`：左侧固定侧边栏（宽约 200px）+ 右侧内容区。
  - 侧边栏：顶部 brand（claude-register），下方 4 个导航项，每项内联 SVG 图标 + 文字，active 高亮。
  - 视图状态与 `location.hash` 双向同步（`#/register`、`#/accounts`、`#/nodes`、`#/settings`），刷新/直链不丢页面；无效或空 hash 落到 `#/register`。
- 响应式：视口 <720px 时侧边栏折叠为顶部横条（图标导航）。
- 登录页 `Login.jsx` 不变（无侧边栏）。

## 页面拆分（`web/src/pages/`）

现 `Dashboard.jsx`（472 行，三大块）一拆为二，`Settings.jsx` 里的 xui fieldset 移出：

| 页面 | 内容 | 来源 |
|---|---|---|
| `Register.jsx` | 开始注册表单、SSE 实时日志、运行历史与详情（截图） | Dashboard 上/中块 |
| `Accounts.jsx` | 账号列表、编辑/复制/重跑/接管/导出、接管状态条 | Dashboard 下块 |
| `Nodes.jsx` | 3x-ui 代理池：启用 toggle、参数（有效期/端口范围）、节点卡片列表、测试连接、清理过期 inbound | Settings 的 xui fieldset |
| `Settings.jsx` | 三张分组卡：面板（密码/端口）、AnyMail 邮箱（key/base/域名/有效期）、注册参数（超时/自动登录/正则/注册代理） | Settings 其余字段 |

注意跨页联动：Accounts 的「重跑」会启动运行（现逻辑 attach 到 Dashboard 的日志流）。拆分后重跑成功即跳转到注册页并附加日志流——通过 App 层传递 `navigate(view)` 与一个共享的「当前运行」状态（提升 `activeRunId` 到 App 或用简单的模块级 store）。取简单方案：把运行流状态（activeRunId/status/logLines/SSE）提升为 App 内自定义 hook，`Register` 展示，`Accounts` 触发后调用 `navigate("register")`。

## 数据流

- API 层（`api.js`）不动。
- 服务端 `PUT /api/config` 已支持部分字段合并（`save_config` 只覆盖出现的字段；密钥空/占位不改；节点密码占位按 base_url 沿用旧值）。
- `Nodes.jsx` 只加载/提交 `xui_enabled、xui_expiry_days、xui_port_min、xui_port_max、xui_nodes`。
- `Settings.jsx` 只提交自己的字段，沿用 `••••` 占位符逻辑。

## 视觉（style.css 重写，纯 CSS）

- Design token（CSS 变量）：背景三层（页/卡/输入）、主色蓝、成功/失败/警告色、边框色、圆角（8/12px）、间距刻度、字号刻度、等宽字体栈。
- 自定义 toggle 开关替换所有 checkbox。
- 节点由「一行五个裸 input」改为节点卡片：每字段带标签，密码型字段掩码，卡片右上角 测试/删除。
- 统一按钮体系：primary / 默认 / danger / ghost / small；统一输入框、徽章（running 蓝 / success 绿 / failed 红 / needs_manual 黄）。
- 日志面板：等宽、深底、细滚动条。

## 错误处理

沿用现有模式：每页 inline success/error 消息条；SSE onerror 静默关闭；409 等状态码文案照旧。不引入 toast。

## 测试与验证

- 项目 web 端无测试设施，不新增。
- 验证：`bun run dev` 起 vite 开发服务器，逐页人工核对（导航、hash 路由、注册流日志、账号操作、节点增删测保存、设置分组保存、窄屏折叠）。不跑生产构建（规约：构建交给 CI）。
- 实现走 git worktree。

## 不做（YAGNI）

- 不加亮色主题/主题切换。
- 不引入路由库、组件库、状态库。
- 不改后端。
