# 侧边栏/滚动区 shadcn 化 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 手写侧边栏换官方 Sidebar 组件，滚动列表换 ScrollArea，徽章/接管条换 Badge/Alert。

**Architecture:** 纯视觉层，handler/API/路由逻辑零改动。sidebar/scroll-area/alert 由 shadcn CLI vendor；`index.css` 补 `--sidebar-*` 变量。

**Tech Stack:** 同上轮：React 18 + Vite 5 + Tailwind v4 + shadcn（zinc 深色）。

**Spec:** `docs/superpowers/specs/2026-07-31-shadcn-sidebar-scrollarea-design.md`

## Global Constraints

- ⚠️ 不跑生产构建；验证 = `bun run dev` + curl 转换 + playwright。
- 逻辑零改动（state、handler、props、hash 路由不动）。
- 提交信息末尾带既定 Happy 双署名格式（同仓库近期提交）。

---

### Task 1: 组件与主题变量

**Files:**
- Create（CLI）: `web/src/components/ui/{sidebar,scroll-area,alert,sheet,skeleton}.jsx`、`web/src/hooks/use-mobile.js`（或同名 .jsx，CLI 决定）
- Modify: `web/src/index.css`

**Interfaces:**
- Produces: `SidebarProvider/Sidebar/SidebarHeader/SidebarContent/SidebarGroup/SidebarMenu/SidebarMenuItem/SidebarMenuButton/SidebarRail/SidebarInset/SidebarTrigger`；`ScrollArea`；`Alert/AlertDescription`；CSS 变量 `--sidebar-*`。

- [ ] **Step 1: CLI 拉组件**

```bash
cd web && bunx --bun shadcn@latest add sidebar scroll-area alert
```
失败重试 3 次；确认生成 .jsx（tsx:false）。若 sidebar.jsx 引用 `@/hooks/use-mobile` 而文件生成在别处，调整 import 或移动文件。

- [ ] **Step 2: index.css 补 sidebar 变量**

`:root` 块末尾追加：

```css
  --sidebar: oklch(0.985 0 0);
  --sidebar-foreground: oklch(0.141 0.005 285.823);
  --sidebar-primary: oklch(0.21 0.006 285.885);
  --sidebar-primary-foreground: oklch(0.985 0 0);
  --sidebar-accent: oklch(0.967 0.001 286.375);
  --sidebar-accent-foreground: oklch(0.21 0.006 285.885);
  --sidebar-border: oklch(0.92 0.004 286.32);
  --sidebar-ring: oklch(0.705 0.015 286.067);
```

`.dark` 块末尾追加：

```css
  --sidebar: oklch(0.21 0.006 285.885);
  --sidebar-foreground: oklch(0.985 0 0);
  --sidebar-primary: oklch(0.488 0.243 264.376);
  --sidebar-primary-foreground: oklch(0.985 0 0);
  --sidebar-accent: oklch(0.274 0.006 286.033);
  --sidebar-accent-foreground: oklch(0.985 0 0);
  --sidebar-border: oklch(1 0 0 / 10%);
  --sidebar-ring: oklch(0.552 0.016 285.938);
```

`@theme inline` 块末尾追加：

```css
  --color-sidebar: var(--sidebar);
  --color-sidebar-foreground: var(--sidebar-foreground);
  --color-sidebar-primary: var(--sidebar-primary);
  --color-sidebar-primary-foreground: var(--sidebar-primary-foreground);
  --color-sidebar-accent: var(--sidebar-accent);
  --color-sidebar-accent-foreground: var(--sidebar-accent-foreground);
  --color-sidebar-border: var(--sidebar-border);
  --color-sidebar-ring: var(--sidebar-ring);
```

- [ ] **Step 3: 验证 + Commit**

```bash
cd web && (bun run dev &) && sleep 3
curl -sf http://localhost:5173/src/components/ui/sidebar.jsx | head -1
curl -sf http://localhost:5173/src/components/ui/scroll-area.jsx | head -1
curl -sf http://localhost:5173/src/components/ui/alert.jsx | head -1
```
停 dev server。

```bash
git add -A web && git commit -m "feat(web): 引入 shadcn sidebar/scroll-area/alert 组件与 sidebar 主题变量"
```

---

### Task 2: App 壳换官方 Sidebar

**Files:**
- Modify: `web/src/App.jsx`（仅布局 return 块；NAV/hash/effect/navigate 保留）

**Interfaces:**
- Consumes: Task 1 全部 Sidebar 件。
- Produces: 页面 props 不变。

- [ ] **Step 1: 重写 App 布局**

imports 增加：

```jsx
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarRail,
  SidebarTrigger,
} from "@/components/ui/sidebar";
import { Separator } from "@/components/ui/separator";
```

`cn` import 若不再使用则删除。登录前分支不变。登录后 return 换成：

```jsx
const current = NAV.find((n) => n.key === view);
return (
  <SidebarProvider>
    <Sidebar collapsible="icon">
      <SidebarHeader>
        <div className="px-2 py-1.5 font-bold tracking-wide group-data-[collapsible=icon]:hidden">
          claude-register
          <span className="block text-[11px] font-normal text-muted-foreground">
            管理面板
          </span>
        </div>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarMenu>
            {NAV.map(({ key, label, Icon }) => (
              <SidebarMenuItem key={key}>
                <SidebarMenuButton
                  isActive={view === key}
                  tooltip={label}
                  onClick={() => navigate(key)}
                >
                  <Icon />
                  <span>{label}</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
            ))}
          </SidebarMenu>
        </SidebarGroup>
      </SidebarContent>
      <SidebarRail />
    </Sidebar>
    <SidebarInset>
      <header className="flex h-12 shrink-0 items-center gap-2 border-b px-4">
        <SidebarTrigger className="-ml-1" />
        <Separator orientation="vertical" className="mr-1 h-4" />
        <span className="text-sm font-medium">{current?.label}</span>
      </header>
      <div className="max-w-5xl min-w-0 flex-1 px-8 py-6 max-md:px-4">
        {view === "register" && <Register runStream={runStream} />}
        {view === "accounts" && (
          <Accounts
            attach={runStream.attach}
            running={runStream.activeStatus === "running"}
            navigate={navigate}
          />
        )}
        {view === "nodes" && <Nodes />}
        {view === "settings" && <Settings />}
      </div>
    </SidebarInset>
    <Toaster position="top-right" />
  </SidebarProvider>
);
```

页内 `<h1 className="mb-5 text-xl font-semibold">` 标题与顶栏标题重复，四页（Register/Accounts/Nodes/Settings）删除各自 h1。

- [ ] **Step 2: 验证 + Commit**

```bash
cd web && (bun run dev &) && sleep 3 && curl -sf http://localhost:5173/src/App.jsx | head -1
```
停 dev server。

```bash
git add web/src && git commit -m "feat(web): App 壳换 shadcn Sidebar（icon 折叠 + 移动端抽屉 + 顶栏 Trigger）"
```

---

### Task 3: ScrollArea / Badge / Alert 替换

**Files:**
- Modify: `web/src/components/status-badge.jsx`
- Modify: `web/src/pages/Register.jsx`
- Modify: `web/src/pages/Accounts.jsx`

- [ ] **Step 1: StatusBadge 基于 Badge**

```jsx
// web/src/components/status-badge.jsx（整文件）
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const STATUS = {
  running: { label: "运行中", cls: "bg-blue-500/15 text-blue-400" },
  success: { label: "成功", cls: "bg-emerald-500/15 text-emerald-400" },
  failed: { label: "失败", cls: "bg-red-500/15 text-red-400" },
  needs_manual: { label: "需人工介入", cls: "bg-amber-500/15 text-amber-400" },
};

export function StatusBadge({ status }) {
  const s = STATUS[status] || { label: status || "未知", cls: "bg-muted text-muted-foreground" };
  return (
    <Badge className={cn("gap-1.5 rounded-full border-transparent", s.cls)}>
      <span
        className={cn(
          "size-1.5 rounded-full bg-current",
          status === "running" && "animate-pulse",
        )}
      />
      {s.label}
    </Badge>
  );
}
```

- [ ] **Step 2: Register.jsx 换 ScrollArea**

imports 加 `import { ScrollArea } from "@/components/ui/scroll-area";`。三处替换：

1. 实时日志（自动滚动改从 ScrollArea 根取 Radix viewport）：

```jsx
useEffect(() => {
  const vp = logPanelRef.current?.querySelector("[data-radix-scroll-area-viewport]");
  if (vp) vp.scrollTop = vp.scrollHeight;
}, [logLines]);
```

```jsx
<ScrollArea
  ref={logPanelRef}
  className="log-panel h-64 rounded-lg border bg-black/40 text-muted-foreground"
>
  <div className="p-3">
    {logLines.length === 0 ? (
      <div className="text-muted-foreground/60">等待日志输出…</div>
    ) : (
      logLines.map((line, i) => <div key={i}>{line}</div>)
    )}
  </div>
</ScrollArea>
```

2. 运行历史列表 `<ul className="flex max-h-80 flex-col gap-1.5 overflow-y-auto">` →

```jsx
<ScrollArea className="max-h-80">
  <ul className="flex flex-col gap-1.5 pr-3">…原 li 不变…</ul>
</ScrollArea>
```

3. 运行详情 `<pre className="log-panel mt-2 max-h-80 overflow-y-auto …">` →

```jsx
<ScrollArea className="log-panel mt-2 max-h-80 rounded-lg border bg-black/40 text-muted-foreground">
  <pre className="p-3">{selectedRun.log || "（无日志）"}</pre>
</ScrollArea>
```

- [ ] **Step 3: Accounts.jsx 换 ScrollArea/Badge/Alert**

imports 加：

```jsx
import { ScrollArea } from "@/components/ui/scroll-area";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
```

1. 账号列表 `<ul className="flex max-h-[560px] flex-col gap-1.5 overflow-x-hidden overflow-y-auto">` →

```jsx
<ScrollArea className="max-h-[560px]">
  <ul className="flex flex-col gap-1.5 pr-3">…原 li 不变…</ul>
</ScrollArea>
```

2. LiveBadge 内 span → Badge：

```jsx
function LiveBadge({ status, checkedAt, detail }) {
  if (!status) return null;
  return (
    <Badge
      className={cn("rounded-full border-transparent", LIVE_CLS[status] || LIVE_CLS.error)}
      title={detail || ""}
    >
      {LIVE_LABEL[status] || status}
      {checkedAt ? (
        <span className="ml-1 font-normal opacity-70">· {relTime(checkedAt)}</span>
      ) : null}
    </Badge>
  );
}
```

3. 「代理」标签 `<span className="rounded-full border px-1.5 text-[11px]">代理</span>` → `<Badge variant="outline" className="rounded-full px-1.5 text-[11px]">代理</Badge>`。

4. 接管条 div → Alert：

```jsx
<Alert className="border-blue-500/30 bg-blue-500/10 text-blue-400 [&>div]:w-full">
  <AlertDescription className="flex w-full items-center justify-between gap-2 text-blue-400">
    <span>正在接管：{takeover.email}</span>
    <span className="flex items-center gap-2">
      <Button variant="outline" size="sm" asChild>
        <a href="/vnc/?autoconnect=1&resize=scale" target="_blank" rel="noopener noreferrer">
          打开画面
        </a>
      </Button>
      <Button variant="outline" size="sm" onClick={stopTakeover}>
        结束接管
      </Button>
    </span>
  </AlertDescription>
</Alert>
```

- [ ] **Step 4: 验证 + Commit**

```bash
cd web && (bun run dev &) && sleep 3
curl -sf http://localhost:5173/src/pages/Register.jsx | head -1
curl -sf http://localhost:5173/src/pages/Accounts.jsx | head -1
```
停 dev server。

```bash
git add web/src && git commit -m "feat(web): 列表/日志换 ScrollArea，徽章换 Badge，接管条换 Alert"
```

---

### Task 4: 全量走查 + 回归

- [ ] **Step 1: playwright 走查**

起 dev + serve.py（bootstrap），复用 `shot2.py` 增加：点 SidebarTrigger 折叠再展开截图（`button[data-sidebar="trigger"]`）。核对四页布局、折叠态图标+tooltip、滚动条为主题色细条、toast 正常、无 JS 错误。发现问题就地修并入下一提交。

- [ ] **Step 2: pytest**

```bash
/opt/claude-register/.venv/bin/python -m pytest tests/ -q
```
Expected: 337 passed。

- [ ] **Step 3: 收尾 Commit（如有修复）**

```bash
git add -A web && git commit -m "fix(web): 走查修复"
```
