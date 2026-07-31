# shadcn/ui 迁移实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 前端视觉层迁移到 shadcn/ui（Tailwind v4 + Radix，zinc 深色预设），交互升级为 AlertDialog 确认 + Sonner toast。

**Architecture:** 业务逻辑、`api.js`、hash 路由、`useRunStream` 完全不动，只重写各页 JSX 的视觉层。shadcn 组件 vendor 进 `web/src/components/ui/`（CLI 拉取，`tsx:false` 出 .jsx）。迁移期间新旧样式共存（`style.css` 保留 import 到最后一个任务才删），保证每个任务结束时页面可用。

**Tech Stack:** React 18 + Vite 5 + Tailwind CSS v4（`@tailwindcss/vite`）+ shadcn/ui（new-york/zinc）+ lucide-react + sonner。包管理 bun。

**Spec:** `docs/superpowers/specs/2026-07-31-shadcn-migration-design.md`

## Global Constraints

- ⚠️ 不跑生产构建（`vite build` 禁止）。验证 = `bun run dev` + `curl -sf http://localhost:5173/src/<file>` 看 vite 转换是否报错 + playwright 截图。
- 固定深色（`<html class="dark">`），不做主题切换。
- toast 文案沿用现有文案；表单初始加载失败仍用页内提示。
- 页面 handler 逻辑保持不变——从现文件原样保留（计划中注明行号）；只有输出渠道 setMessage/setError → toast 的替换按对照表执行。
- shadcn registry（ui.shadcn.com）拉取失败时重试 3 次；仍失败则停下报告，不手写猜测组件。
- 提交信息末尾带:

  ```
  Generated with [Claude Code](https://claude.ai/code)
  via [Happy](https://happy.engineering)

  Co-Authored-By: Claude <noreply@anthropic.com>
  Co-Authored-By: Happy <yesreply@happy.engineering>
  ```

---

### Task 1: Tailwind v4 + shadcn 脚手架

**Files:**
- Modify: `web/package.json`（bun add）
- Modify: `web/vite.config.js`
- Create: `web/jsconfig.json`
- Create: `web/components.json`
- Create: `web/src/index.css`
- Modify: `web/index.html`（html 加 class="dark"）
- Modify: `web/src/main.jsx`（加 import "./index.css"，保留 style.css）
- Create（CLI 生成）: `web/src/lib/utils.js`、`web/src/components/ui/*.jsx`

**Interfaces:**
- Produces: `@/` 别名；`cn()`（`@/lib/utils`）；ui 组件 `button card input label switch badge separator alert-dialog sonner tooltip`；CSS 变量主题（zinc 深色）。

- [ ] **Step 1: 安装依赖**

```bash
cd web
bun add tailwindcss @tailwindcss/vite tw-animate-css class-variance-authority clsx tailwind-merge lucide-react sonner
```

- [ ] **Step 2: vite 配置加 tailwind 插件与 @ 别名**

```js
// web/vite.config.js（整文件）
import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": path.resolve(import.meta.dirname, "src") },
  },
  build: { outDir: "dist" },
  server: {
    proxy: {
      "/api": "http://localhost:8790",
      "/runs": "http://localhost:8790",
    },
  },
});
```

- [ ] **Step 3: jsconfig + components.json**

```json
// web/jsconfig.json
{
  "compilerOptions": {
    "baseUrl": ".",
    "paths": { "@/*": ["./src/*"] }
  }
}
```

```json
// web/components.json
{
  "$schema": "https://ui.shadcn.com/schema.json",
  "style": "new-york",
  "rsc": false,
  "tsx": false,
  "tailwind": {
    "config": "",
    "css": "src/index.css",
    "baseColor": "zinc",
    "cssVariables": true,
    "prefix": ""
  },
  "aliases": {
    "components": "@/components",
    "utils": "@/lib/utils",
    "ui": "@/components/ui",
    "lib": "@/lib",
    "hooks": "@/hooks"
  },
  "iconLibrary": "lucide"
}
```

- [ ] **Step 4: index.css（Tailwind v4 + shadcn zinc 主题）**

```css
/* web/src/index.css */
@import "tailwindcss";
@import "tw-animate-css";

@custom-variant dark (&:is(.dark *));

:root {
  --radius: 0.625rem;
  --background: oklch(1 0 0);
  --foreground: oklch(0.141 0.005 285.823);
  --card: oklch(1 0 0);
  --card-foreground: oklch(0.141 0.005 285.823);
  --popover: oklch(1 0 0);
  --popover-foreground: oklch(0.141 0.005 285.823);
  --primary: oklch(0.21 0.006 285.885);
  --primary-foreground: oklch(0.985 0 0);
  --secondary: oklch(0.967 0.001 286.375);
  --secondary-foreground: oklch(0.21 0.006 285.885);
  --muted: oklch(0.967 0.001 286.375);
  --muted-foreground: oklch(0.552 0.016 285.938);
  --accent: oklch(0.967 0.001 286.375);
  --accent-foreground: oklch(0.21 0.006 285.885);
  --destructive: oklch(0.577 0.245 27.325);
  --border: oklch(0.92 0.004 286.32);
  --input: oklch(0.92 0.004 286.32);
  --ring: oklch(0.705 0.015 286.067);
}

.dark {
  --background: oklch(0.141 0.005 285.823);
  --foreground: oklch(0.985 0 0);
  --card: oklch(0.21 0.006 285.885);
  --card-foreground: oklch(0.985 0 0);
  --popover: oklch(0.21 0.006 285.885);
  --popover-foreground: oklch(0.985 0 0);
  --primary: oklch(0.92 0.004 286.32);
  --primary-foreground: oklch(0.21 0.006 285.885);
  --secondary: oklch(0.274 0.006 286.033);
  --secondary-foreground: oklch(0.985 0 0);
  --muted: oklch(0.274 0.006 286.033);
  --muted-foreground: oklch(0.705 0.015 286.067);
  --accent: oklch(0.274 0.006 286.033);
  --accent-foreground: oklch(0.985 0 0);
  --destructive: oklch(0.704 0.191 22.216);
  --border: oklch(1 0 0 / 10%);
  --input: oklch(1 0 0 / 15%);
  --ring: oklch(0.552 0.016 285.938);
}

@theme inline {
  --radius-sm: calc(var(--radius) - 4px);
  --radius-md: calc(var(--radius) - 2px);
  --radius-lg: var(--radius);
  --radius-xl: calc(var(--radius) + 4px);
  --color-background: var(--background);
  --color-foreground: var(--foreground);
  --color-card: var(--card);
  --color-card-foreground: var(--card-foreground);
  --color-popover: var(--popover);
  --color-popover-foreground: var(--popover-foreground);
  --color-primary: var(--primary);
  --color-primary-foreground: var(--primary-foreground);
  --color-secondary: var(--secondary);
  --color-secondary-foreground: var(--secondary-foreground);
  --color-muted: var(--muted);
  --color-muted-foreground: var(--muted-foreground);
  --color-accent: var(--accent);
  --color-accent-foreground: var(--accent-foreground);
  --color-destructive: var(--destructive);
  --color-border: var(--border);
  --color-input: var(--input);
  --color-ring: var(--ring);
}

@layer base {
  * {
    @apply border-border outline-ring/50;
  }
  body {
    @apply bg-background text-foreground;
  }
}

/* ===== 项目自定义（shadcn 覆盖不到的） ===== */
.log-panel {
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
  font-size: 12.5px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
  scrollbar-width: thin;
  scrollbar-color: var(--border) transparent;
}
```

- [ ] **Step 5: index.html 固定深色 + main.jsx 引入 index.css**

`web/index.html`：`<html lang="zh-CN">` → `<html lang="zh-CN" class="dark">`（若无 lang 就在 `<html>` 上直接加 `class="dark"`）。
`web/src/main.jsx`：在 `import "./style.css"` 之后加一行 `import "./index.css";`（style.css 保留到 Task 6 删除）。

- [ ] **Step 6: 拉取 shadcn 组件**

```bash
cd web
bunx --bun shadcn@latest add button card input label switch badge separator alert-dialog sonner tooltip
```

Expected: `src/lib/utils.js` 与 `src/components/ui/{button,card,input,label,switch,badge,separator,alert-dialog,sonner,tooltip}.jsx` 生成（tsx:false → .jsx）。失败重试 3 次，仍失败停下报告。

- [ ] **Step 7: sonner 去掉 next-themes 依赖**

CLI 生成的 `src/components/ui/sonner.jsx` 会 `import { useTheme } from "next-themes"`（Next.js 专用）。整文件替换为：

```jsx
// web/src/components/ui/sonner.jsx
import { Toaster as Sonner } from "sonner";

const Toaster = ({ ...props }) => {
  return (
    <Sonner
      theme="dark"
      className="toaster group"
      style={{
        "--normal-bg": "var(--popover)",
        "--normal-text": "var(--popover-foreground)",
        "--normal-border": "var(--border)",
      }}
      {...props}
    />
  );
};

export { Toaster };
```

- [ ] **Step 8: 验证**

```bash
cd web && (bun run dev &) && sleep 3
curl -sf http://localhost:5173/src/index.css | head -2
curl -sf http://localhost:5173/src/components/ui/button.jsx | head -2
curl -sf http://localhost:5173/src/components/ui/sonner.jsx | head -2
curl -sf http://localhost:5173/ | grep -o 'class="dark"'
```
Expected: 均有输出无报错。验证完停 dev server。

- [ ] **Step 9: Commit**

```bash
git add -A web
git commit -m "feat(web): Tailwind v4 + shadcn/ui 脚手架（zinc 深色）"
```

---

### Task 2: StatusBadge 共享组件 + App 壳迁移

**Files:**
- Create: `web/src/components/status-badge.jsx`
- Modify: `web/src/App.jsx`（整文件重写）
- Modify: `web/src/pages/Register.jsx`（删除 StatusBadge 定义与 export，改为 import）
- Modify: `web/src/pages/Accounts.jsx`（StatusBadge import 路径改为新组件）

**Interfaces:**
- Consumes: Task 1 的 ui 组件、`cn()`、Toaster。
- Produces: `<StatusBadge status={string} />`（`@/components/status-badge`）；App 继续给 `Register` 传 `runStream`、给 `Accounts` 传 `attach/running/navigate`（签名不变）。

- [ ] **Step 1: StatusBadge**

```jsx
// web/src/components/status-badge.jsx
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
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold whitespace-nowrap",
        s.cls,
      )}
    >
      <span
        className={cn(
          "size-1.5 rounded-full bg-current",
          status === "running" && "animate-pulse",
        )}
      />
      {s.label}
    </span>
  );
}
```

- [ ] **Step 2: App.jsx 整文件重写**

保留现有逻辑（authed 检查、viewFromHash、hashchange effect、navigate、useRunStream），只换视觉层与图标。现 `App.jsx` 中 NAV 的内联 `<svg>` 全部删除。

```jsx
// web/src/App.jsx
import { useEffect, useState } from "react";
import { Play, Users, Server, Settings as SettingsIcon } from "lucide-react";
import { api } from "./api.js";
import { useRunStream } from "./hooks/useRunStream.js";
import { cn } from "@/lib/utils";
import { Toaster } from "@/components/ui/sonner";
import Login from "./pages/Login.jsx";
import Register from "./pages/Register.jsx";
import Accounts from "./pages/Accounts.jsx";
import Nodes from "./pages/Nodes.jsx";
import Settings from "./pages/Settings.jsx";

const NAV = [
  { key: "register", label: "注册", Icon: Play },
  { key: "accounts", label: "账号", Icon: Users },
  { key: "nodes", label: "节点", Icon: Server },
  { key: "settings", label: "设置", Icon: SettingsIcon },
];

const VIEW_KEYS = NAV.map((n) => n.key);

function viewFromHash() {
  const v = window.location.hash.replace(/^#\/?/, "");
  return VIEW_KEYS.includes(v) ? v : "register";
}

export default function App() {
  const [authed, setAuthed] = useState(null); // null=checking, false=need login, true=ok
  const [view, setView] = useState(viewFromHash);
  const runStream = useRunStream();

  useEffect(() => {
    api
      .getConfig()
      .then(() => setAuthed(true))
      .catch(() => setAuthed(false));
  }, []);

  useEffect(() => {
    const onHash = () => setView(viewFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  function navigate(next) {
    window.location.hash = `#/${next}`;
  }

  if (authed === null) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="text-muted-foreground">加载中…</div>
      </div>
    );
  }

  if (!authed) {
    return <Login onOk={() => setAuthed(true)} />;
  }

  return (
    <div className="flex min-h-screen max-md:flex-col">
      <aside className="flex w-52 shrink-0 flex-col gap-1 border-r bg-card px-3 py-5 max-md:w-full max-md:flex-row max-md:items-center max-md:overflow-x-auto max-md:border-r-0 max-md:border-b max-md:px-3 max-md:py-2">
        <div className="px-3 pb-4 font-bold tracking-wide max-md:whitespace-nowrap max-md:pb-0">
          claude-register
          <span className="block text-[11px] font-normal text-muted-foreground max-md:hidden">
            管理面板
          </span>
        </div>
        {NAV.map(({ key, label, Icon }) => (
          <button
            key={key}
            onClick={() => navigate(key)}
            className={cn(
              "flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground",
              view === key && "bg-accent font-semibold text-foreground",
            )}
          >
            <Icon className="size-4 shrink-0" />
            <span className="max-md:hidden">{label}</span>
          </button>
        ))}
      </aside>
      <main className="min-w-0 flex-1 px-8 py-7 max-md:px-4 max-md:py-4 max-w-5xl">
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
      </main>
      <Toaster position="top-right" />
    </div>
  );
}
```

- [ ] **Step 3: Register/Accounts 改用共享 StatusBadge**

`web/src/pages/Register.jsx`：删除文件顶部 `STATUS_LABEL` 常量与 `export function StatusBadge(...)`（现 4-17 行），加 `import { StatusBadge } from "@/components/status-badge";`。
`web/src/pages/Accounts.jsx`：`import { StatusBadge } from "./Register.jsx";` → `import { StatusBadge } from "@/components/status-badge";`。

- [ ] **Step 4: 验证 + Commit**

```bash
cd web && (bun run dev &) && sleep 3
curl -sf http://localhost:5173/src/App.jsx | head -2
curl -sf http://localhost:5173/src/components/status-badge.jsx | head -2
```
Expected: 无报错（此时页面视觉新旧混合，正常）。停 dev server。

```bash
git add -A web/src
git commit -m "feat(web): App 壳迁移 shadcn——Tailwind 侧边栏 + lucide 图标 + Toaster + 共享 StatusBadge"
```

---

### Task 3: Register 页迁移

**Files:**
- Modify: `web/src/pages/Register.jsx`（整文件重写）

**Interfaces:**
- Consumes: `Card/CardHeader/CardTitle/CardContent`、`Button`、`Input`、`StatusBadge`、`toast`（sonner）。
- Produces: `<Register runStream />` 默认导出，props 不变。

- [ ] **Step 1: 整文件重写**

handler 逻辑（startRun/openRun/两个 useEffect）与现文件一致；`setStartError` 改 `toast.error`，startError state 删除。

```jsx
// web/src/pages/Register.jsx
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { api } from "../api.js";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

export default function Register({ runStream }) {
  const { activeRunId, activeStatus, logLines, attach, streamEpoch } = runStream;
  const [domain, setDomain] = useState("");
  const [email, setEmail] = useState("");
  const [starting, setStarting] = useState(false);
  const [runs, setRuns] = useState([]);
  const [selectedRun, setSelectedRun] = useState(null);
  const logPanelRef = useRef(null);

  useEffect(() => {
    api.listRuns().then(setRuns).catch(() => {});
  }, [streamEpoch]);

  useEffect(() => {
    if (logPanelRef.current) {
      logPanelRef.current.scrollTop = logPanelRef.current.scrollHeight;
    }
  }, [logLines]);

  async function startRun() {
    setStarting(true);
    try {
      const res = await api.startRun(email || undefined, domain || undefined);
      attach(res.run_id);
    } catch (err) {
      toast.error(err.status === 409 ? "已有任务在运行" : "启动失败，请重试");
    } finally {
      setStarting(false);
    }
  }

  async function openRun(id) {
    try {
      setSelectedRun(await api.runDetail(id));
    } catch {
      setSelectedRun(null);
    }
  }

  return (
    <>
      <h1 className="mb-5 text-xl font-semibold">注册</h1>
      <div className="grid items-start gap-5 min-[1200px]:grid-cols-[5fr_7fr]">
        <Card>
          <CardHeader>
            <CardTitle>开始注册</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <Input
              placeholder="邮箱后缀 / domain（可选）"
              value={domain}
              onChange={(e) => setDomain(e.target.value)}
            />
            <Input
              placeholder="已有邮箱（可选，用于重跑该账号）"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
            <Button
              onClick={startRun}
              disabled={starting || activeStatus === "running"}
            >
              {starting ? "启动中…" : "开始注册"}
            </Button>

            {activeRunId && (
              <div className="pt-2">
                <div className="mb-2 flex items-center justify-between text-sm text-muted-foreground">
                  <span>
                    运行 <code className="font-mono text-xs">{activeRunId}</code>
                  </span>
                  <StatusBadge status={activeStatus} />
                </div>
                <div
                  ref={logPanelRef}
                  className="log-panel h-64 overflow-y-auto rounded-lg border bg-black/40 p-3 text-muted-foreground"
                >
                  {logLines.length === 0 ? (
                    <div className="text-muted-foreground/60">等待日志输出…</div>
                  ) : (
                    logLines.map((line, i) => <div key={i}>{line}</div>)
                  )}
                </div>
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>运行历史</CardTitle>
          </CardHeader>
          <CardContent>
            {runs.length === 0 ? (
              <div className="text-sm text-muted-foreground">暂无运行记录</div>
            ) : (
              <ul className="flex max-h-80 flex-col gap-1.5 overflow-y-auto">
                {runs.map((r) => (
                  <li
                    key={r.id}
                    onClick={() => openRun(r.id)}
                    className="flex cursor-pointer items-center justify-between gap-2 rounded-lg border bg-background/50 px-3 py-2.5 text-sm transition-colors hover:border-ring"
                  >
                    <span className="flex min-w-0 flex-col gap-0.5">
                      <span className="font-mono text-xs">{r.id}</span>
                      <span className="truncate text-xs text-muted-foreground">
                        {r.email || "（未指定邮箱）"} {r.domain ? `@${r.domain}` : ""}
                      </span>
                    </span>
                    <StatusBadge status={r.status} />
                  </li>
                ))}
              </ul>
            )}

            {selectedRun && (
              <div className="mt-4 border-t pt-4">
                <div className="mb-2 flex items-center justify-between">
                  <h3 className="text-sm font-semibold">
                    运行详情 <span className="font-mono text-xs">{selectedRun.id}</span>
                  </h3>
                  <Button variant="ghost" size="sm" onClick={() => setSelectedRun(null)}>
                    关闭
                  </Button>
                </div>
                <StatusBadge status={selectedRun.status} />
                <pre className="log-panel mt-2 max-h-80 overflow-y-auto rounded-lg border bg-black/40 p-3 text-muted-foreground">
                  {selectedRun.log || "（无日志）"}
                </pre>
                {selectedRun.screenshots && selectedRun.screenshots.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-2">
                    {selectedRun.screenshots.map((name) => (
                      <a
                        key={name}
                        href={`/runs/${selectedRun.id}/${name}`}
                        target="_blank"
                        rel="noreferrer"
                      >
                        <img
                          className="block h-[90px] w-[140px] rounded-md border object-cover"
                          src={`/runs/${selectedRun.id}/${name}`}
                          alt={name}
                        />
                      </a>
                    ))}
                  </div>
                )}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </>
  );
}
```

- [ ] **Step 2: 验证 + Commit**

```bash
cd web && (bun run dev &) && sleep 3 && curl -sf http://localhost:5173/src/pages/Register.jsx | head -2
```
停 dev server。

```bash
git add web/src/pages/Register.jsx
git commit -m "feat(web): 注册页迁移 shadcn，启动报错改 toast"
```

---

### Task 4: Accounts 页迁移（AlertDialog + toast）

**Files:**
- Modify: `web/src/pages/Accounts.jsx`（整文件重写）

**Interfaces:**
- Consumes: `Card`、`Button`、`Input`、`Label`、`AlertDialog*`、`StatusBadge`、`toast`。
- Produces: `<Accounts attach running navigate />` 默认导出，props 不变。

- [ ] **Step 1: 整文件重写**

handler 逻辑与现文件一致，改动：
- 错误 state（takeoverError/rerunError/exportError/editError）全删 → `toast.error(同文案)`；复制成功仍用按钮文字变「已复制」（不 toast，避免高频打扰）。
- `deleteAccount` 的 `window.confirm` → AlertDialog：`deleteTarget` state 存待删 email，对话框确认后执行删除，成功 `toast.success("已删除 " + email)`。
- 重跑成功 `toast.success("已开始重跑，正在跳转…")`。

```jsx
// web/src/pages/Accounts.jsx
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Download } from "lucide-react";
import { api } from "../api.js";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

const EDIT_FIELDS = [
  ["display_name", "备注", "给账号起个名字", false],
  ["password", "密码", "登录密码", false],
  ["session_key", "sessionKey", "sk-ant-sid01-…", true],
  ["proxy", "代理", "socks5://user:pass@host:port", true],
];

export default function Accounts({ attach, running, navigate }) {
  const [accounts, setAccounts] = useState([]);
  const [takeover, setTakeover] = useState({ running: false, email: null });
  const [copiedEmail, setCopiedEmail] = useState("");
  const [editingEmail, setEditingEmail] = useState("");
  const [editForm, setEditForm] = useState({});
  const [deleteTarget, setDeleteTarget] = useState("");

  function refreshLists() {
    api.listAccounts().then(setAccounts).catch(() => {});
    api.takeoverStatus().then(setTakeover).catch(() => {});
  }

  useEffect(() => {
    refreshLists();
  }, []);

  async function doRerun(acctEmail) {
    try {
      const res = await api.rerun(acctEmail);
      attach(res.run_id);
      toast.success("已开始重跑，正在跳转…");
      navigate("register");
    } catch (err) {
      toast.error(
        err.status === 409
          ? `「${acctEmail}」重跑失败：已有任务在运行`
          : `「${acctEmail}」重跑失败`,
      );
    }
  }

  async function copyLine(acct) {
    const text = acct.text || acct.email;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
      } else {
        // http 面板无 clipboard API，退回 execCommand
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      }
      setCopiedEmail(acct.email);
      setTimeout(() => setCopiedEmail(""), 1500);
    } catch {
      toast.error("复制失败，请手动复制");
    }
  }

  const startTakeover = async (acctEmail) => {
    try {
      await api.takeoverStart(acctEmail);
      setTakeover(await api.takeoverStatus());
    } catch (e) {
      toast.error(
        e.status === 409
          ? "已有接管会话，请先结束"
          : e.status === 400
            ? "该账号无 sessionKey"
            : e.status === 403
              ? "接管功能已禁用"
              : `启动接管失败（${e.status || "?"}）`,
      );
    }
  };

  const stopTakeover = async () => {
    try {
      await api.takeoverStop();
      setTakeover({ running: false, email: null });
    } catch {
      /* ignore */
    }
  };

  const startEdit = (a) => {
    setEditingEmail(a.email);
    setEditForm({
      display_name: a.display_name || "",
      password: a.password || "",
      session_key: a.session_key || "",
      proxy: a.proxy || "",
    });
  };

  const saveEdit = async () => {
    try {
      await api.accountUpdate(editingEmail, editForm);
      setEditingEmail("");
      refreshLists();
      toast.success("已保存");
    } catch (e) {
      toast.error(`保存失败（${e.status || "?"}）`);
    }
  };

  const confirmDelete = async () => {
    const email = deleteTarget;
    setDeleteTarget("");
    try {
      await api.accountDelete(email);
      setEditingEmail("");
      refreshLists();
      toast.success(`已删除 ${email}`);
    } catch (e) {
      toast.error(`删除失败（${e.status || "?"}）`);
    }
  };

  async function exportAll() {
    try {
      const text = await api.exportAccountsText();
      const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "accounts.txt";
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      toast.error("导出失败，请重试");
    }
  }

  return (
    <>
      <h1 className="mb-5 text-xl font-semibold">账号</h1>
      <Card>
        <CardHeader className="flex-row items-center justify-between space-y-0">
          <CardTitle>账号列表</CardTitle>
          {accounts.length > 0 && (
            <Button variant="outline" size="sm" onClick={exportAll}>
              <Download /> 导出全部
            </Button>
          )}
        </CardHeader>
        <CardContent className="space-y-3">
          {takeover.running && (
            <div className="flex items-center justify-between gap-2 rounded-lg bg-blue-500/15 px-3 py-2 text-sm text-blue-400">
              <span>正在接管：{takeover.email}</span>
              <span className="flex items-center gap-2">
                <Button variant="outline" size="sm" asChild>
                  <a
                    href="/vnc/?autoconnect=1&resize=scale"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    打开画面
                  </a>
                </Button>
                <Button variant="outline" size="sm" onClick={stopTakeover}>
                  结束接管
                </Button>
              </span>
            </div>
          )}
          {accounts.length === 0 ? (
            <div className="text-sm text-muted-foreground">暂无账号</div>
          ) : (
            <ul className="flex max-h-[560px] flex-col gap-1.5 overflow-y-auto overflow-x-hidden">
              {accounts.map((a) => (
                <li key={a.email} className="flex flex-col">
                  <div
                    className={`flex items-center justify-between gap-2 rounded-lg border bg-background/50 px-3 py-2.5 text-sm ${
                      editingEmail === a.email ? "rounded-b-none border-ring" : ""
                    }`}
                  >
                    <span className="flex min-w-0 flex-col gap-1 overflow-hidden">
                      <span className="truncate" title={a.email}>
                        {a.email}
                      </span>
                      <span className="flex items-center gap-2 overflow-hidden whitespace-nowrap text-xs text-muted-foreground">
                        <StatusBadge status={a.status} />
                        {a.display_name ? <span>{a.display_name}</span> : null}
                        {a.session_key ? (
                          <span className="font-mono">
                            sk {String(a.session_key).slice(7, 17)}…
                          </span>
                        ) : null}
                        {a.proxy ? (
                          <span className="rounded-full border px-1.5 text-[11px]">代理</span>
                        ) : null}
                      </span>
                    </span>
                    <span className="flex shrink-0 gap-1.5">
                      <Button variant="outline" size="sm" onClick={() => copyLine(a)}>
                        {copiedEmail === a.email ? "已复制" : "复制"}
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        className={editingEmail === a.email ? "border-ring text-foreground" : ""}
                        onClick={() =>
                          editingEmail === a.email ? setEditingEmail("") : startEdit(a)
                        }
                      >
                        {editingEmail === a.email ? "收起" : "编辑"}
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => doRerun(a.email)}
                        disabled={running}
                      >
                        重跑
                      </Button>
                      {a.session_key && (
                        <Button
                          variant="outline"
                          size="sm"
                          className="border-blue-500/50 text-blue-400 hover:text-blue-300"
                          onClick={() => startTakeover(a.email)}
                        >
                          接管
                        </Button>
                      )}
                    </span>
                  </div>
                  {editingEmail === a.email && (
                    <div className="flex flex-col gap-3 rounded-b-lg border border-t-0 border-ring bg-background/50 p-3.5">
                      <div className="grid grid-cols-2 gap-2.5">
                        {EDIT_FIELDS.map(([key, label, hint, wide]) => (
                          <div
                            key={key}
                            className={`flex min-w-0 flex-col gap-1 ${wide ? "col-span-2" : ""}`}
                          >
                            <Label className="text-[11px] uppercase tracking-wide text-muted-foreground">
                              {label}
                            </Label>
                            <Input
                              className={wide ? "font-mono text-xs" : ""}
                              value={editForm[key] ?? ""}
                              placeholder={hint}
                              spellCheck={false}
                              onChange={(e) =>
                                setEditForm((f) => ({ ...f, [key]: e.target.value }))
                              }
                            />
                          </div>
                        ))}
                      </div>
                      <div className="flex items-center gap-2">
                        <Button size="sm" onClick={saveEdit}>
                          保存
                        </Button>
                        <Button variant="outline" size="sm" onClick={() => setEditingEmail("")}>
                          取消
                        </Button>
                        <span className="flex-1" />
                        <Button
                          variant="destructive"
                          size="sm"
                          onClick={() => setDeleteTarget(a.email)}
                        >
                          删除账号
                        </Button>
                      </div>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <AlertDialog open={!!deleteTarget} onOpenChange={(o) => !o && setDeleteTarget("")}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除账号？</AlertDialogTitle>
            <AlertDialogDescription>
              确认删除账号 {deleteTarget}？此操作不可恢复。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-white hover:bg-destructive/90"
              onClick={confirmDelete}
            >
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
```

- [ ] **Step 2: 验证 + Commit**

```bash
cd web && (bun run dev &) && sleep 3 && curl -sf http://localhost:5173/src/pages/Accounts.jsx | head -2
```
停 dev server。

```bash
git add web/src/pages/Accounts.jsx
git commit -m "feat(web): 账号页迁移 shadcn——AlertDialog 删除确认 + toast 提示"
```

---

### Task 5: Nodes + Settings 页迁移

**Files:**
- Modify: `web/src/pages/Nodes.jsx`（整文件重写）
- Modify: `web/src/pages/Settings.jsx`（整文件重写）

**Interfaces:**
- Consumes: `Card`、`Button`、`Input`、`Label`、`Switch`、`Separator`、`toast`。
- Produces: `<Nodes />`、`<Settings />` 无 props 默认导出。

- [ ] **Step 1: Nodes.jsx 整文件重写**

数据逻辑（XUI_KEYS 挑字段、部分提交、testNode/cleanupExpired/save）与现文件一致；message/error state 删除 → toast；Toggle → Switch；测试/删除按钮加 lucide 图标。

```jsx
// web/src/pages/Nodes.jsx
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { FlaskConical, Plus, Trash2, Eraser } from "lucide-react";
import { api } from "../api.js";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";

const XUI_KEYS = ["xui_enabled", "xui_expiry_days", "xui_port_min", "xui_port_max", "xui_nodes"];

const POOL_FIELDS = [
  { key: "xui_expiry_days", label: "代理有效期（天）" },
  { key: "xui_port_min", label: "端口范围下限" },
  { key: "xui_port_max", label: "端口范围上限" },
];

const NODE_FIELDS = [
  { key: "name", label: "名称", placeholder: "如 usa-4" },
  { key: "base_url", label: "面板地址", placeholder: "http://host:2053" },
  { key: "username", label: "用户名", placeholder: "admin" },
  { key: "password", label: "密码", placeholder: "••••", type: "password" },
  { key: "proxy_host", label: "代理出口主机", placeholder: "对外连接用的 host" },
];

const EMPTY_NODE = { name: "", base_url: "", username: "", password: "", proxy_host: "" };

export default function Nodes() {
  const [form, setForm] = useState(null);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState("");

  useEffect(() => {
    api
      .getConfig()
      .then((cfg) => {
        const picked = {};
        for (const k of XUI_KEYS) picked[k] = cfg[k];
        picked.xui_nodes = cfg.xui_nodes || [];
        setForm(picked);
      })
      .catch(() => setLoadError("加载配置失败"));
  }, []);

  function setField(key, value) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  function setNode(i, key, value) {
    const nodes = [...form.xui_nodes];
    nodes[i] = { ...nodes[i], [key]: value };
    setField("xui_nodes", nodes);
  }

  async function save() {
    setSaving(true);
    try {
      const updated = await api.putConfig(form);
      const picked = {};
      for (const k of XUI_KEYS) picked[k] = updated[k];
      picked.xui_nodes = updated.xui_nodes || [];
      setForm(picked);
      toast.success("已保存");
    } catch {
      toast.error("保存失败，请重试");
    } finally {
      setSaving(false);
    }
  }

  async function testNode(node) {
    try {
      const r = await api.xuiTest(node);
      toast.success(`节点连接成功，现有 ${r.inbound_count} 个 inbound`);
    } catch (e) {
      toast.error(`节点连接失败：${e.body?.detail || e.message}`);
    }
  }

  async function cleanupExpired() {
    try {
      const r = await api.xuiCleanup();
      toast.success(`已清理过期 inbound：共 ${r.total} 个`);
    } catch {
      toast.error("清理失败，请重试");
    }
  }

  if (loadError) return <div className="text-sm text-destructive">{loadError}</div>;
  if (!form) return <div className="text-sm text-muted-foreground">加载中…</div>;

  return (
    <>
      <h1 className="mb-5 text-xl font-semibold">节点</h1>

      <Card className="mb-5">
        <CardHeader>
          <CardTitle>代理池设置</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center justify-between">
            <Label htmlFor="xui_enabled">启用 3x-ui 代理池</Label>
            <Switch
              id="xui_enabled"
              checked={!!form.xui_enabled}
              onCheckedChange={(v) => setField("xui_enabled", v)}
            />
          </div>
          {POOL_FIELDS.map((f) => (
            <div className="space-y-1.5" key={f.key}>
              <Label htmlFor={f.key}>{f.label}</Label>
              <Input
                id={f.key}
                type="number"
                value={form[f.key] ?? ""}
                onChange={(e) => setField(f.key, Number(e.target.value))}
              />
            </div>
          ))}
        </CardContent>
      </Card>

      <Card className="mb-5">
        <CardHeader className="flex-row items-center justify-between space-y-0">
          <CardTitle>节点列表</CardTitle>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setField("xui_nodes", [...form.xui_nodes, { ...EMPTY_NODE }])}
          >
            <Plus /> 添加节点
          </Button>
        </CardHeader>
        <CardContent className="space-y-3">
          {form.xui_nodes.length === 0 ? (
            <div className="text-sm text-muted-foreground">暂无节点，点右上角添加</div>
          ) : (
            form.xui_nodes.map((node, i) => (
              <div key={i} className="rounded-xl border bg-background/50 p-4">
                <div className="mb-3 flex items-center justify-between">
                  <span className="text-sm font-semibold">
                    {node.name || `节点 ${i + 1}`}
                  </span>
                  <span className="flex gap-1.5">
                    <Button variant="outline" size="sm" onClick={() => testNode(node)}>
                      <FlaskConical /> 测试
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      className="text-destructive hover:text-destructive"
                      onClick={() =>
                        setField("xui_nodes", form.xui_nodes.filter((_, j) => j !== i))
                      }
                    >
                      <Trash2 /> 删除
                    </Button>
                  </span>
                </div>
                <div className="grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-2.5">
                  {NODE_FIELDS.map((f) => (
                    <div className="flex flex-col gap-1" key={f.key}>
                      <Label className="text-xs text-muted-foreground">{f.label}</Label>
                      <Input
                        type={f.type || "text"}
                        placeholder={f.placeholder}
                        value={node[f.key] ?? ""}
                        onChange={(e) => setNode(i, f.key, e.target.value)}
                      />
                    </div>
                  ))}
                </div>
              </div>
            ))
          )}
          <Button variant="ghost" size="sm" onClick={cleanupExpired}>
            <Eraser /> 清理过期 inbound
          </Button>
        </CardContent>
      </Card>

      <Button onClick={save} disabled={saving}>
        {saving ? "保存中…" : "保存节点配置"}
      </Button>
    </>
  );
}
```

- [ ] **Step 2: Settings.jsx 整文件重写**

GROUPS/OWN_KEYS/密钥占位逻辑与现文件一致；message/error → toast；checkbox → Switch。

```jsx
// web/src/pages/Settings.jsx
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { api } from "../api.js";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";

const SECRET_PLACEHOLDER = "••••";
const SECRET_FIELDS = ["panel_password", "anymail_api_key"];

const GROUPS = [
  {
    title: "面板",
    fields: [
      { key: "panel_password", label: "面板密码", type: "password", secret: true },
      { key: "panel_port", label: "面板端口", type: "number" },
    ],
  },
  {
    title: "AnyMail 邮箱",
    fields: [
      { key: "anymail_api_key", label: "AnyMail API Key", type: "password", secret: true },
      { key: "anymail_base_url", label: "AnyMail Base URL", type: "text" },
      { key: "anymail_domain", label: "AnyMail 域名", type: "text" },
      { key: "anymail_expires_hours", label: "邮箱有效期（小时，0=永久）", type: "number" },
    ],
  },
  {
    title: "注册参数",
    fields: [
      { key: "register_login_timeout", label: "登录超时（秒）", type: "number" },
      { key: "register_auto_login", label: "注册后自动登录", type: "checkbox" },
      { key: "register_code_regex", label: "验证码正则", type: "text" },
      {
        key: "register_proxy",
        label: "注册代理（留空直连）",
        type: "text",
        placeholder: "http://user:pass@host:port 或 socks5://host:port",
      },
    ],
  },
];

const OWN_KEYS = GROUPS.flatMap((g) => g.fields.map((f) => f.key));

export default function Settings() {
  const [form, setForm] = useState(null);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState("");

  useEffect(() => {
    api
      .getConfig()
      .then((cfg) => {
        const picked = {};
        for (const k of OWN_KEYS) picked[k] = cfg[k];
        setForm(picked);
      })
      .catch(() => setLoadError("加载配置失败"));
  }, []);

  function setField(key, value) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  async function save(e) {
    e.preventDefault();
    setSaving(true);
    const payload = { ...form };
    for (const key of SECRET_FIELDS) {
      if (payload[key] === SECRET_PLACEHOLDER || payload[key] === undefined) {
        delete payload[key];
      }
    }
    try {
      const updated = await api.putConfig(payload);
      const picked = {};
      for (const k of OWN_KEYS) picked[k] = updated[k];
      setForm(picked);
      toast.success("已保存");
    } catch {
      toast.error("保存失败，请重试");
    } finally {
      setSaving(false);
    }
  }

  if (loadError) return <div className="text-sm text-destructive">{loadError}</div>;
  if (!form) return <div className="text-sm text-muted-foreground">加载中…</div>;

  return (
    <>
      <h1 className="mb-5 text-xl font-semibold">设置</h1>
      <form onSubmit={save}>
        {GROUPS.map((group) => (
          <Card className="mb-5" key={group.title}>
            <CardHeader>
              <CardTitle>{group.title}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {group.fields.map((f) =>
                f.type === "checkbox" ? (
                  <div className="flex items-center justify-between" key={f.key}>
                    <Label htmlFor={f.key}>{f.label}</Label>
                    <Switch
                      id={f.key}
                      checked={!!form[f.key]}
                      onCheckedChange={(v) => setField(f.key, v)}
                    />
                  </div>
                ) : (
                  <div className="space-y-1.5" key={f.key}>
                    <Label htmlFor={f.key}>{f.label}</Label>
                    <Input
                      id={f.key}
                      type={f.type}
                      placeholder={f.secret ? SECRET_PLACEHOLDER : f.placeholder ?? ""}
                      value={form[f.key] ?? ""}
                      onChange={(e) =>
                        setField(
                          f.key,
                          f.type === "number"
                            ? e.target.value.replace(/[^0-9.]/g, "")
                            : e.target.value,
                        )
                      }
                    />
                  </div>
                ),
              )}
            </CardContent>
          </Card>
        ))}
        <Button type="submit" disabled={saving}>
          {saving ? "保存中…" : "保存设置"}
        </Button>
      </form>
    </>
  );
}
```

- [ ] **Step 3: 验证 + Commit**

```bash
cd web && (bun run dev &) && sleep 3
curl -sf http://localhost:5173/src/pages/Nodes.jsx | head -2
curl -sf http://localhost:5173/src/pages/Settings.jsx | head -2
```
停 dev server。

```bash
git add web/src/pages/Nodes.jsx web/src/pages/Settings.jsx
git commit -m "feat(web): 节点/设置页迁移 shadcn——Switch + toast"
```

---

### Task 6: Login 迁移 + 旧样式清理 + 全量走查

**Files:**
- Modify: `web/src/pages/Login.jsx`（换 shadcn 控件）
- Modify: `web/src/main.jsx`（删 style.css import）
- Delete: `web/src/style.css`、`web/src/components/Toggle.jsx`

- [ ] **Step 1: Login.jsx 迁移**

现 Login.jsx 逻辑保留（读现文件，44 行：password state + submit 调 `api.login`），视觉换成：

```jsx
// web/src/pages/Login.jsx（JSX 结构；handler 保留现文件逻辑）
import { useState } from "react";
import { toast } from "sonner";
import { api } from "../api.js";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

export default function Login({ onOk }) {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    try {
      await api.login(password);
      onOk();
    } catch {
      toast.error("密码错误");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <Card className="w-80">
        <CardHeader className="text-center">
          <CardTitle className="text-xl">claude-register</CardTitle>
          <p className="text-sm text-muted-foreground">输入面板密码继续</p>
        </CardHeader>
        <CardContent>
          <form className="flex flex-col gap-3" onSubmit={submit}>
            <Input
              type="password"
              placeholder="面板密码"
              value={password}
              autoFocus
              onChange={(e) => setPassword(e.target.value)}
            />
            <Button type="submit" disabled={busy}>
              {busy ? "登录中…" : "登录"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
```

⚠️ 先读现 `Login.jsx` 核对 handler（api.login 的函数名/参数、错误处理），以现文件为准，只换视觉层；Toaster 在 App 中未登录分支也要能显示——把 `<Toaster position="top-right" />` 从登录后布局移到 App 最外层（两个分支都包含），即：`if (!authed) return (<><Login onOk={...} /><Toaster position="top-right" /></>);`，登录后布局里保持原位或同样用 Fragment 包裹，二选一保持只渲染一个 Toaster。

- [ ] **Step 2: 删旧样式**

```bash
git rm web/src/style.css web/src/components/Toggle.jsx
```
`web/src/main.jsx`：删除 `import "./style.css";`。
全局 grep 确认无残留引用：`grep -rn "style.css\|Toggle" web/src/ --include="*.jsx"` 应无结果（Toggle 匹配到 shadcn 无关组件名时忽略）。

- [ ] **Step 3: playwright 全量走查**

起 `bun run dev` + 后端 `python serve.py`（bootstrap 模式），跑上一轮的截图脚本（scratchpad `shot.py`，四页 + 添加节点 + 窄屏 + 无效 hash），另加交互冒烟：
- 节点页点「测试」→ 应出现 sonner toast（`[data-sonner-toast]` 可见）
- 账号页若有账号：点「删除账号」→ AlertDialog 出现（`[role="alertdialog"]`）；无账号则跳过
Expected: 无 JS 错误、截图布局正常、toast/dialog 出现。发现问题就地修复。

- [ ] **Step 4: pytest 回归**

```bash
/opt/claude-register/.venv/bin/python -m pytest tests/ -q
```
Expected: 320 passed。

- [ ] **Step 5: Commit**

```bash
git add -A web
git commit -m "feat(web): 登录页迁移 shadcn，删除旧手写样式与 Toggle"
```
