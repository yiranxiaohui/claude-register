# 侧边栏与剩余 UI 全量 shadcn 化 设计

日期：2026-07-31
状态：已确认

## 目标

把上一轮迁移后仍手写的 UI 全部换成 shadcn 组件；修掉列表原生白色滚动条。逻辑零改动。

## 改动

- 新增组件：`bunx shadcn add sidebar scroll-area alert`（sidebar 连带 sheet/skeleton/use-mobile hook；生成的 TSX 附属文件若为 .tsx 需确认 tsx:false 生成 .jsx）。`index.css` 补 zinc 预设的 `--sidebar-*` 变量（:root 与 .dark 两组）+ `@theme inline` 映射。
- **App.jsx**：手写 aside → `SidebarProvider` + `Sidebar collapsible="icon"` + `SidebarHeader`(brand)/`SidebarContent`/`SidebarMenu`/`SidebarMenuButton isActive` + `SidebarRail`；内容区用 `SidebarInset`，顶部一条含 `SidebarTrigger` 的小工具栏。移动端由 Sidebar 内置 Sheet 抽屉接管，删除手写 max-md 横条样式。
- **ScrollArea** 替换 `overflow-y-auto`：注册页运行历史列表、运行详情日志 pre、实时日志面板、账号页账号列表。
- **StatusBadge/LiveBadge**（`components/status-badge.jsx`、Accounts 内 LiveBadge）：改基于 `Badge` 组件 + 定制配色 class；「代理」标签 → `Badge variant="outline"`。
- **接管状态条** → `Alert`（含操作按钮）。

## 验证

playwright 四页走查 + 侧边栏折叠/展开冒烟 + toast 冒烟 + pytest 全绿；不跑生产构建。走 worktree，完成后合并 main、push、CI 出镜像、部署 10014。

## 不做

- 不改任何 handler/API/路由逻辑。
- 不加主题切换。
