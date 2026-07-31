# 管理面板迁移 shadcn/ui 设计

日期：2026-07-31
状态：已确认（顺带升级交互 + shadcn 默认深色预设）

## 目标

前端视觉层从手写 CSS 迁移到 shadcn/ui（Tailwind v4 + Radix），交互顺带升级（AlertDialog 确认、Sonner toast），采用 shadcn 默认 zinc 深色主题。业务逻辑、API 层、hash 路由、useRunStream 全部不动。

## 技术栈与脚手架

- Tailwind CSS v4：`@tailwindcss/vite` 插件接入 vite，无独立 tailwind.config；主样式入口 `web/src/index.css`（`@import "tailwindcss"` + shadcn 主题变量 + tw-animate-css）。
- shadcn CLI：`bunx --bun shadcn@latest init`（new-york 风格、zinc 基色、CSS 变量模式），组件 vendor 进 `web/src/components/ui/`。
- 固定深色：`index.html` 的 `<html>` 加 `class="dark"`，不做主题切换。
- 路径别名：vite `resolve.alias` 配 `@` → `web/src`，加 `web/jsconfig.json`（shadcn CLI 的约定要求）。
- 新依赖（bun 安装）：`tailwindcss`、`@tailwindcss/vite`、`tw-animate-css`、`class-variance-authority`、`clsx`、`tailwind-merge`、`lucide-react`、`sonner`、各 radix 原语（由 shadcn add 引入）。
- 引入组件：button、card、input、label、switch、badge、separator、alert-dialog、sonner、tooltip。（dialog 不需要——账号编辑保留行内展开。）

## 页面重构（视觉层）

- `App.jsx`：侧边栏/导航用 Tailwind 类重写；内联 SVG 换 `lucide-react`（Play、Users、Server、Settings）；全局挂 `<Toaster />`（sonner，深色）。
- `Register.jsx`：卡片/输入/按钮换 shadcn；StatusBadge 改为基于 `Badge` 的自定义变体组件（running 蓝/success 绿/failed 红/needs_manual 黄，保留脉冲圆点）；日志面板保留自定义样式（等宽深底细滚动条，进 index.css）。
- `Accounts.jsx`：列表布局保留（不表格化）；行内编辑面板保留但控件换 shadcn；删除账号 `window.confirm` → `AlertDialog`；复制成功、导出失败、重跑失败、接管报错等全部 → toast。
- `Nodes.jsx`：Toggle → `Switch`；节点卡片用 `Card`；测试/清理/保存结果 → toast；测试/删除按钮带 lucide 图标（FlaskConical/Trash2）。
- `Settings.jsx`：三张分组卡用 `Card`；checkbox → `Switch`；保存结果 → toast。
- 删除：`web/src/style.css`、`web/src/components/Toggle.jsx`。
- 页内 success/error 消息条全部移除，统一 toast；表单加载失败仍用页内提示（页面主体没内容时 toast 不够）。

## 错误处理

- toast 文案沿用现有文案（"已保存"、"节点连接失败：…"、"已有任务在运行" 等）。
- SSE、409/400/403 分支逻辑不变，只是输出渠道改 toast。

## 测试与验证

- `bun run dev` + playwright 截图走查四页 + 窄屏 + 无 JS 错误 + hash 回退（沿用上轮脚本）。
- 交互冒烟：删除弹 AlertDialog、保存弹 toast。
- 后端不动，pytest 320 条应保持全绿。不跑生产构建（CI 负责）；CI 无需改动（vite 插件链内完成 Tailwind）。
- 实现走 git worktree。

## 不做（YAGNI）

- 主题切换 / 亮色模式
- 路由库、状态库
- 账号列表表格化、Dialog 化编辑
- 后端与 API 层改动
