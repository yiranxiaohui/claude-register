# 侧边栏布局 + 模块拆分 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 管理面板改为左侧边栏四模块布局（注册/账号/节点/设置），3x-ui 节点独立成页，视觉精细化。

**Architecture:** 纯前端改动（`web/src/`）。`App.jsx` 持有侧边栏 + hash 路由 + 共享运行流 hook；`Dashboard.jsx` 拆为 `Register.jsx` 与 `Accounts.jsx`；`Settings.jsx` 拆出 xui 部分成 `Nodes.jsx`。服务端 `PUT /api/config` 已支持部分字段合并（`server/config_store.py:89 save_config`），各页只提交自己字段。

**Tech Stack:** React 18 + Vite 5，纯 CSS（无组件库/路由库），包管理用 bun。

**Spec:** `docs/superpowers/specs/2026-07-31-sidebar-modules-design.md`

## Global Constraints

- 零新 npm 依赖；不改 `web/src/api.js`；不改后端。
- 只保持深色主题，沿用 `style.css:1-23` 现有 CSS 变量色板。
- ⚠️ 不跑生产构建（`vite build` 禁止）。验证方式：`bun run dev` 起开发服务器后 `curl -s http://localhost:5173/src/pages/X.jsx | head -5` —— vite 按需转换，语法错误会在响应/终端报错。
- web 端无测试设施，本计划不写自动化测试；每个任务以 vite 转换无错 + 人工核对为验收。
- 首次需在 `web/` 目录 `bun install`（node_modules 不存在）。
- 提交信息末尾带:

  ```
  Generated with [Claude Code](https://claude.ai/code)
  via [Happy](https://happy.engineering)

  Co-Authored-By: Claude <noreply@anthropic.com>
  Co-Authored-By: Happy <yesreply@happy.engineering>
  ```

---

### Task 1: 样式基建 + Toggle 组件

**Files:**
- Modify: `web/src/style.css`（追加，不删既有规则）
- Create: `web/src/components/Toggle.jsx`

**Interfaces:**
- Produces: `<Toggle checked={bool} onChange={(bool)=>void} id?={string} />`；CSS 类 `.sidebar .nav-item .nav-icon .content-area .toggle .node-card .settings-group-card .form-field-inline .card-actions`

- [ ] **Step 1: 建 Toggle 组件**

```jsx
// web/src/components/Toggle.jsx
export default function Toggle({ checked, onChange, id }) {
  return (
    <button
      type="button"
      id={id}
      role="switch"
      aria-checked={checked}
      className={`toggle${checked ? " on" : ""}`}
      onClick={() => onChange(!checked)}
    >
      <span className="toggle-knob" />
    </button>
  );
}
```

- [ ] **Step 2: style.css 追加新样式块**（文件末尾追加）

```css
/* ===== Sidebar layout ===== */
.layout {
  display: flex;
  min-height: 100vh;
}
.sidebar {
  width: 208px;
  flex-shrink: 0;
  background: var(--bg-elevated);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  padding: 20px 12px;
  gap: 4px;
  position: sticky;
  top: 0;
  height: 100vh;
}
.sidebar-brand {
  font-weight: 700;
  font-size: 15px;
  padding: 4px 12px 18px;
  letter-spacing: 0.2px;
}
.sidebar-brand .brand-sub {
  display: block;
  font-size: 11px;
  font-weight: 400;
  color: var(--text-faint);
  margin-top: 2px;
}
.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--text-dim);
  font-size: 14px;
  cursor: pointer;
  text-align: left;
  transition: background 0.15s, color 0.15s;
}
.nav-item:hover {
  background: var(--bg-card);
  color: var(--text);
}
.nav-item.active {
  background: var(--running-bg);
  color: var(--accent-hover);
  font-weight: 600;
}
.nav-icon {
  width: 17px;
  height: 17px;
  flex-shrink: 0;
}
.content-area {
  flex: 1;
  min-width: 0;
  padding: 28px 32px 48px;
  max-width: 1080px;
}
.page-title {
  margin: 0 0 20px;
  font-size: 20px;
}

/* ===== Toggle switch ===== */
.toggle {
  width: 40px;
  height: 22px;
  border-radius: 11px;
  border: 1px solid var(--border);
  background: var(--bg);
  padding: 2px;
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s;
  display: inline-flex;
}
.toggle-knob {
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: var(--text-faint);
  transition: transform 0.18s, background 0.15s;
}
.toggle.on {
  background: var(--accent);
  border-color: var(--accent);
}
.toggle.on .toggle-knob {
  transform: translateX(18px);
  background: #fff;
}

/* ===== Node cards ===== */
.node-card {
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg-elevated);
  padding: 16px;
  margin-bottom: 12px;
}
.node-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}
.node-card-title {
  font-weight: 600;
  font-size: 14px;
}
.node-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 10px;
}
.node-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.node-field .field-label {
  font-size: 12px;
  color: var(--text-dim);
}

/* ===== Settings group cards ===== */
.settings-group-card {
  margin-bottom: 20px;
}
.form-field-inline {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 6px 0;
}
.card-actions {
  display: flex;
  gap: 10px;
  margin-top: 14px;
  flex-wrap: wrap;
}

/* ===== Responsive ===== */
@media (max-width: 720px) {
  .layout {
    flex-direction: column;
  }
  .sidebar {
    width: 100%;
    height: auto;
    position: static;
    flex-direction: row;
    align-items: center;
    padding: 8px 12px;
    border-right: none;
    border-bottom: 1px solid var(--border);
    overflow-x: auto;
  }
  .sidebar-brand {
    padding: 0 8px;
    white-space: nowrap;
  }
  .sidebar-brand .brand-sub {
    display: none;
  }
  .nav-item span.nav-label {
    display: none;
  }
  .content-area {
    padding: 16px;
  }
}
```

- [ ] **Step 3: 验证 vite 转换**

```bash
cd web && bun install && (bun run dev &) && sleep 3
curl -sf http://localhost:5173/src/components/Toggle.jsx | head -3
curl -sf http://localhost:5173/src/style.css | tail -3
```
Expected: 两个 curl 都输出转换后内容，无 500/报错。验证完 `kill %1`（或 pkill -f "vite"）。

- [ ] **Step 4: Commit**

```bash
git add web/src/components/Toggle.jsx web/src/style.css
git commit -m "feat(web): 样式基建——侧边栏/toggle/节点卡片样式 + Toggle 组件"
```

---

### Task 2: useRunStream hook

**Files:**
- Create: `web/src/hooks/useRunStream.js`

**Interfaces:**
- Produces: `useRunStream()` 返回 `{ activeRunId, activeStatus, logLines, attach(runId), streamEpoch }`。
  - `attach(runId)`：关旧 SSE、清日志、订阅 `/api/runs/{id}/stream`。
  - `streamEpoch`：number，流结束（done 事件）时 +1，页面用它触发列表刷新。
  - `activeStatus`：`"running" | "success" | "failed" | "needs_manual" | null`。

- [ ] **Step 1: 实现 hook**（逻辑来自现 `web/src/pages/Dashboard.jsx:63-83` 的 `attachStream`）

```js
// web/src/hooks/useRunStream.js
import { useCallback, useEffect, useRef, useState } from "react";

// 全局唯一的注册运行日志流：App 持有，注册页展示，账号页触发重跑后 attach。
export function useRunStream() {
  const [activeRunId, setActiveRunId] = useState(null);
  const [activeStatus, setActiveStatus] = useState(null);
  const [logLines, setLogLines] = useState([]);
  const [streamEpoch, setStreamEpoch] = useState(0);
  const esRef = useRef(null);

  useEffect(() => {
    return () => {
      if (esRef.current) esRef.current.close();
    };
  }, []);

  const attach = useCallback((runId) => {
    if (esRef.current) esRef.current.close();
    setLogLines([]);
    setActiveRunId(runId);
    setActiveStatus("running");
    const es = new EventSource(`/api/runs/${runId}/stream`);
    esRef.current = es;
    es.addEventListener("log", (e) => {
      setLogLines((prev) => [...prev, e.data]);
    });
    es.addEventListener("done", (e) => {
      setActiveStatus(e.data || "success");
      es.close();
      esRef.current = null;
      setStreamEpoch((n) => n + 1);
    });
    es.onerror = () => {
      es.close();
      esRef.current = null;
    };
  }, []);

  return { activeRunId, activeStatus, logLines, attach, streamEpoch };
}
```

- [ ] **Step 2: 验证 vite 转换**

```bash
cd web && (bun run dev &) && sleep 3
curl -sf http://localhost:5173/src/hooks/useRunStream.js | head -3
```
Expected: 输出转换后 JS，无报错。验证完停掉 dev server。

- [ ] **Step 3: Commit**

```bash
git add web/src/hooks/useRunStream.js
git commit -m "feat(web): 提取共享运行日志流 hook useRunStream"
```

---

### Task 3: Nodes 页 + 精简 Settings 页

**Files:**
- Create: `web/src/pages/Nodes.jsx`
- Modify: `web/src/pages/Settings.jsx`（整文件重写）

**Interfaces:**
- Consumes: Task 1 的 `Toggle`、`.node-card` 等样式类。
- Produces: `<Nodes />`、`<Settings />` 无 props 默认导出组件（App 在 Task 4 引用）。
- 二者都只提交自己负责的字段（服务端合并式 PUT）。

- [ ] **Step 1: 写 Nodes.jsx**

```jsx
// web/src/pages/Nodes.jsx
import { useEffect, useState } from "react";
import { api } from "../api.js";
import Toggle from "../components/Toggle.jsx";

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
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .getConfig()
      .then((cfg) => {
        const picked = {};
        for (const k of XUI_KEYS) picked[k] = cfg[k];
        picked.xui_nodes = cfg.xui_nodes || [];
        setForm(picked);
      })
      .catch(() => setError("加载配置失败"));
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
    setMessage("");
    setError("");
    try {
      const updated = await api.putConfig(form);
      const picked = {};
      for (const k of XUI_KEYS) picked[k] = updated[k];
      picked.xui_nodes = updated.xui_nodes || [];
      setForm(picked);
      setMessage("已保存");
    } catch {
      setError("保存失败，请重试");
    } finally {
      setSaving(false);
    }
  }

  async function testNode(node) {
    setMessage("");
    setError("");
    try {
      const r = await api.xuiTest(node);
      setMessage(`节点连接成功，现有 ${r.inbound_count} 个 inbound`);
    } catch (e) {
      setError(`节点连接失败：${e.body?.detail || e.message}`);
    }
  }

  async function cleanupExpired() {
    setMessage("");
    setError("");
    try {
      const r = await api.xuiCleanup();
      setMessage(`已清理过期 inbound：共 ${r.total} 个`);
    } catch {
      setError("清理失败，请重试");
    }
  }

  if (error && !form) return <div className="error-msg">{error}</div>;
  if (!form) return <div className="empty-hint">加载中…</div>;

  return (
    <>
      <h1 className="page-title">节点</h1>

      <section className="card settings-group-card">
        <h2 className="card-title">代理池设置</h2>
        <div className="form-field-inline">
          <label className="field-label" htmlFor="xui_enabled">启用 3x-ui 代理池</label>
          <Toggle
            id="xui_enabled"
            checked={!!form.xui_enabled}
            onChange={(v) => setField("xui_enabled", v)}
          />
        </div>
        {POOL_FIELDS.map((f) => (
          <div className="form-field" key={f.key}>
            <label className="field-label" htmlFor={f.key}>{f.label}</label>
            <input
              id={f.key}
              className="input"
              type="number"
              value={form[f.key] ?? ""}
              onChange={(e) => setField(f.key, Number(e.target.value))}
            />
          </div>
        ))}
      </section>

      <section className="card settings-group-card">
        <div className="card-header-row">
          <h2 className="card-title">节点列表</h2>
          <button
            type="button"
            className="btn btn-small"
            onClick={() => setField("xui_nodes", [...form.xui_nodes, { ...EMPTY_NODE }])}
          >
            + 添加节点
          </button>
        </div>
        {form.xui_nodes.length === 0 ? (
          <div className="empty-hint">暂无节点，点右上角添加</div>
        ) : (
          form.xui_nodes.map((node, i) => (
            <div className="node-card" key={i}>
              <div className="node-card-head">
                <span className="node-card-title">{node.name || `节点 ${i + 1}`}</span>
                <span className="row-actions">
                  <button type="button" className="btn btn-small" onClick={() => testNode(node)}>
                    测试
                  </button>
                  <button
                    type="button"
                    className="btn btn-small btn-danger"
                    onClick={() =>
                      setField("xui_nodes", form.xui_nodes.filter((_, j) => j !== i))
                    }
                  >
                    删除
                  </button>
                </span>
              </div>
              <div className="node-grid">
                {NODE_FIELDS.map((f) => (
                  <div className="node-field" key={f.key}>
                    <span className="field-label">{f.label}</span>
                    <input
                      className="input"
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
        <div className="card-actions">
          <button type="button" className="btn" onClick={cleanupExpired}>
            清理过期 inbound
          </button>
        </div>
      </section>

      {message && <div className="success-msg">{message}</div>}
      {error && <div className="error-msg">{error}</div>}
      <button className="btn btn-primary" onClick={save} disabled={saving}>
        {saving ? "保存中…" : "保存节点配置"}
      </button>
    </>
  );
}
```

- [ ] **Step 2: 重写 Settings.jsx**（去掉 xui，分三组卡）

```jsx
// web/src/pages/Settings.jsx
import { useEffect, useState } from "react";
import { api } from "../api.js";
import Toggle from "../components/Toggle.jsx";

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
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .getConfig()
      .then((cfg) => {
        const picked = {};
        for (const k of OWN_KEYS) picked[k] = cfg[k];
        setForm(picked);
      })
      .catch(() => setError("加载配置失败"));
  }, []);

  function setField(key, value) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  async function save(e) {
    e.preventDefault();
    setSaving(true);
    setMessage("");
    setError("");
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
      setMessage("已保存");
    } catch {
      setError("保存失败，请重试");
    } finally {
      setSaving(false);
    }
  }

  if (error && !form) return <div className="error-msg">{error}</div>;
  if (!form) return <div className="empty-hint">加载中…</div>;

  return (
    <>
      <h1 className="page-title">设置</h1>
      <form onSubmit={save}>
        {GROUPS.map((group) => (
          <section className="card settings-group-card" key={group.title}>
            <h2 className="card-title">{group.title}</h2>
            {group.fields.map((f) =>
              f.type === "checkbox" ? (
                <div className="form-field-inline" key={f.key}>
                  <label className="field-label" htmlFor={f.key}>{f.label}</label>
                  <Toggle
                    id={f.key}
                    checked={!!form[f.key]}
                    onChange={(v) => setField(f.key, v)}
                  />
                </div>
              ) : (
                <div className="form-field" key={f.key}>
                  <label className="field-label" htmlFor={f.key}>{f.label}</label>
                  <input
                    id={f.key}
                    className="input"
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
          </section>
        ))}
        {message && <div className="success-msg">{message}</div>}
        {error && <div className="error-msg">{error}</div>}
        <button className="btn btn-primary" type="submit" disabled={saving}>
          {saving ? "保存中…" : "保存设置"}
        </button>
      </form>
    </>
  );
}
```

- [ ] **Step 3: 验证 vite 转换**

```bash
cd web && (bun run dev &) && sleep 3
curl -sf http://localhost:5173/src/pages/Nodes.jsx | head -3
curl -sf http://localhost:5173/src/pages/Settings.jsx | head -3
```
Expected: 均输出转换后 JS。验证完停 dev server。

- [ ] **Step 4: Commit**

```bash
git add web/src/pages/Nodes.jsx web/src/pages/Settings.jsx
git commit -m "feat(web): 节点独立成页，设置页按 面板/邮箱/注册 分组"
```

---

### Task 4: Register/Accounts 拆分 + App 侧边栏与 hash 路由

**Files:**
- Create: `web/src/pages/Register.jsx`
- Create: `web/src/pages/Accounts.jsx`
- Modify: `web/src/App.jsx`（整文件重写）
- Delete: `web/src/pages/Dashboard.jsx`

**Interfaces:**
- Consumes: Task 2 `useRunStream()`；Task 3 `Nodes`/`Settings`；Task 1 样式类。
- Produces:
  - `<Register runStream={runStream} />`，`runStream` 即 `useRunStream()` 返回对象。
  - `<Accounts attach={fn(runId)} running={bool} navigate={fn(view)} />`。

- [ ] **Step 1: 写 Register.jsx**

内容来自现 `Dashboard.jsx`：`STATUS_LABEL`/`StatusBadge`（1-17 行）、`startRun`（85-100 行）、`openRun`（102-109 行）、开始注册卡片 + 日志块（228-275 行）、运行历史卡片（277-329 行）。改动点：SSE 状态改用 props `runStream`；`refreshLists` 只刷 runs；`streamEpoch` 变化时刷新。

```jsx
// web/src/pages/Register.jsx
import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";

const STATUS_LABEL = {
  running: "运行中",
  success: "成功",
  failed: "失败",
  needs_manual: "需人工介入",
};

export function StatusBadge({ status }) {
  return (
    <span className={`badge badge-${status || "unknown"}`}>
      {STATUS_LABEL[status] || status || "未知"}
    </span>
  );
}

export default function Register({ runStream }) {
  const { activeRunId, activeStatus, logLines, attach, streamEpoch } = runStream;
  const [domain, setDomain] = useState("");
  const [email, setEmail] = useState("");
  const [startError, setStartError] = useState("");
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
    setStartError("");
    setStarting(true);
    try {
      const res = await api.startRun(email || undefined, domain || undefined);
      attach(res.run_id);
    } catch (err) {
      setStartError(err.status === 409 ? "已有任务在运行" : "启动失败，请重试");
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
      <h1 className="page-title">注册</h1>
      <div className="dashboard-grid">
        <section className="card">
          <h2 className="card-title">开始注册</h2>
          <div className="form-row">
            <input
              className="input"
              placeholder="邮箱后缀 / domain（可选）"
              value={domain}
              onChange={(e) => setDomain(e.target.value)}
            />
            <input
              className="input"
              placeholder="已有邮箱（可选，用于重跑该账号）"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
            <button
              className="btn btn-primary"
              onClick={startRun}
              disabled={starting || activeStatus === "running"}
            >
              {starting ? "启动中…" : "开始注册"}
            </button>
          </div>
          {startError && <div className="error-msg">{startError}</div>}

          {activeRunId && (
            <div className="log-block">
              <div className="log-header">
                <span>
                  运行 <code>{activeRunId}</code>
                </span>
                <StatusBadge status={activeStatus} />
              </div>
              <div className="log-panel" ref={logPanelRef}>
                {logLines.length === 0 ? (
                  <div className="log-empty">等待日志输出…</div>
                ) : (
                  logLines.map((line, i) => (
                    <div className="log-line" key={i}>
                      {line}
                    </div>
                  ))
                )}
              </div>
            </div>
          )}
        </section>

        <section className="card">
          <h2 className="card-title">运行历史</h2>
          {runs.length === 0 ? (
            <div className="empty-hint">暂无运行记录</div>
          ) : (
            <ul className="list">
              {runs.map((r) => (
                <li key={r.id} className="list-row" onClick={() => openRun(r.id)}>
                  <span className="list-main">
                    <span className="mono">{r.id}</span>
                    <span className="list-sub">
                      {r.email || "（未指定邮箱）"} {r.domain ? `@${r.domain}` : ""}
                    </span>
                  </span>
                  <StatusBadge status={r.status} />
                </li>
              ))}
            </ul>
          )}

          {selectedRun && (
            <div className="run-detail">
              <div className="run-detail-header">
                <h3>
                  运行详情 <span className="mono">{selectedRun.id}</span>
                </h3>
                <button className="btn btn-ghost" onClick={() => setSelectedRun(null)}>
                  关闭
                </button>
              </div>
              <StatusBadge status={selectedRun.status} />
              <pre className="log-panel log-static">{selectedRun.log || "（无日志）"}</pre>
              {selectedRun.screenshots && selectedRun.screenshots.length > 0 && (
                <div className="screenshots">
                  {selectedRun.screenshots.map((name) => (
                    <a
                      key={name}
                      href={`/runs/${selectedRun.id}/${name}`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      <img
                        className="screenshot-thumb"
                        src={`/runs/${selectedRun.id}/${name}`}
                        alt={name}
                      />
                    </a>
                  ))}
                </div>
              )}
            </div>
          )}
        </section>
      </div>
    </>
  );
}
```

- [ ] **Step 2: 写 Accounts.jsx**

内容来自现 `Dashboard.jsx` 账号块（125-224 行的函数 + 331-469 行的 JSX），`StatusBadge` 从 Register 导入。改动点：`doRerun` 成功后 `attach(res.run_id)` 并 `navigate("register")`；「重跑」按钮 `disabled={running}`。

```jsx
// web/src/pages/Accounts.jsx
import { useEffect, useState } from "react";
import { api } from "../api.js";
import { StatusBadge } from "./Register.jsx";

export default function Accounts({ attach, running, navigate }) {
  const [accounts, setAccounts] = useState([]);
  const [takeover, setTakeover] = useState({ running: false, email: null });
  const [takeoverError, setTakeoverError] = useState("");
  const [rerunError, setRerunError] = useState("");
  const [copiedEmail, setCopiedEmail] = useState("");
  const [exportError, setExportError] = useState("");
  const [editingEmail, setEditingEmail] = useState("");
  const [editForm, setEditForm] = useState({});
  const [editError, setEditError] = useState("");

  function refreshLists() {
    api.listAccounts().then(setAccounts).catch(() => {});
    api.takeoverStatus().then(setTakeover).catch(() => {});
  }

  useEffect(() => {
    refreshLists();
  }, []);

  async function doRerun(acctEmail) {
    setRerunError("");
    try {
      const res = await api.rerun(acctEmail);
      attach(res.run_id);
      navigate("register");
    } catch (err) {
      setRerunError(
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
      setExportError("复制失败，请手动复制");
    }
  }

  const startTakeover = async (acctEmail) => {
    setTakeoverError("");
    try {
      await api.takeoverStart(acctEmail);
      setTakeover(await api.takeoverStatus());
    } catch (e) {
      setTakeoverError(
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
    setEditError("");
    setEditingEmail(a.email);
    setEditForm({
      display_name: a.display_name || "",
      password: a.password || "",
      session_key: a.session_key || "",
      proxy: a.proxy || "",
    });
  };

  const saveEdit = async () => {
    setEditError("");
    try {
      await api.accountUpdate(editingEmail, editForm);
      setEditingEmail("");
      refreshLists();
    } catch (e) {
      setEditError(`保存失败（${e.status || "?"}）`);
    }
  };

  const deleteAccount = async () => {
    if (!window.confirm(`确认删除账号 ${editingEmail}？此操作不可恢复。`)) return;
    setEditError("");
    try {
      await api.accountDelete(editingEmail);
      setEditingEmail("");
      refreshLists();
    } catch (e) {
      setEditError(`删除失败（${e.status || "?"}）`);
    }
  };

  async function exportAll() {
    setExportError("");
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
      setExportError("导出失败，请重试");
    }
  }

  return (
    <>
      <h1 className="page-title">账号</h1>
      <section className="card">
        <div className="card-header-row">
          <h2 className="card-title">账号列表</h2>
          {accounts.length > 0 && (
            <button className="btn btn-small" onClick={exportAll}>
              导出全部
            </button>
          )}
        </div>
        {takeover.running && (
          <div className="takeover-bar">
            正在接管：{takeover.email}
            <span className="takeover-actions">
              <a
                className="btn btn-small"
                href="/vnc/?autoconnect=1&resize=scale"
                target="_blank"
                rel="noopener noreferrer"
              >
                打开画面
              </a>
              <button className="btn btn-small" onClick={stopTakeover}>
                结束接管
              </button>
            </span>
          </div>
        )}
        {takeoverError && <div className="error-msg">{takeoverError}</div>}
        {rerunError && <div className="error-msg">{rerunError}</div>}
        {exportError && <div className="error-msg">{exportError}</div>}
        {accounts.length === 0 ? (
          <div className="empty-hint">暂无账号</div>
        ) : (
          <ul className="list list-accounts">
            {accounts.map((a) => (
              <li key={a.email} className="list-item">
                <div
                  className={`list-row${editingEmail === a.email ? " list-row-editing" : ""}`}
                >
                  <span className="list-main">
                    <span className="list-email" title={a.email}>
                      {a.email}
                    </span>
                    <span className="list-sub">
                      <StatusBadge status={a.status} />
                      {a.display_name ? <span>{a.display_name}</span> : null}
                      {a.session_key ? (
                        <span className="mono">
                          sk {String(a.session_key).slice(7, 17)}…
                        </span>
                      ) : null}
                      {a.proxy ? <span className="tag">代理</span> : null}
                    </span>
                  </span>
                  <span className="row-actions">
                    <button className="btn btn-small" onClick={() => copyLine(a)}>
                      {copiedEmail === a.email ? "已复制" : "复制"}
                    </button>
                    <button
                      className={`btn btn-small${editingEmail === a.email ? " btn-toggled" : ""}`}
                      onClick={() =>
                        editingEmail === a.email ? setEditingEmail("") : startEdit(a)
                      }
                    >
                      {editingEmail === a.email ? "收起" : "编辑"}
                    </button>
                    <button
                      className="btn btn-small"
                      onClick={() => doRerun(a.email)}
                      disabled={running}
                    >
                      重跑
                    </button>
                    {a.session_key && (
                      <button
                        className="btn btn-small btn-takeover"
                        onClick={() => startTakeover(a.email)}
                      >
                        接管
                      </button>
                    )}
                  </span>
                </div>
                {editingEmail === a.email && (
                  <div className="edit-form">
                    <div className="edit-grid">
                      {[
                        ["display_name", "备注", "给账号起个名字", false],
                        ["password", "密码", "登录密码", false],
                        ["session_key", "sessionKey", "sk-ant-sid01-…", true],
                        ["proxy", "代理", "socks5://user:pass@host:port", true],
                      ].map(([key, label, hint, wide]) => (
                        <label
                          key={key}
                          className={`edit-field${wide ? " edit-field-wide" : ""}`}
                        >
                          <span className="edit-label">{label}</span>
                          <input
                            className={`input${wide ? " mono" : ""}`}
                            value={editForm[key] ?? ""}
                            placeholder={hint}
                            spellCheck={false}
                            onChange={(e) =>
                              setEditForm((f) => ({ ...f, [key]: e.target.value }))
                            }
                          />
                        </label>
                      ))}
                    </div>
                    <div className="edit-actions">
                      <button className="btn btn-small btn-save" onClick={saveEdit}>
                        保存
                      </button>
                      <button className="btn btn-small" onClick={() => setEditingEmail("")}>
                        取消
                      </button>
                      <span className="edit-actions-spacer" />
                      <button className="btn btn-small btn-danger" onClick={deleteAccount}>
                        删除账号
                      </button>
                    </div>
                    {editError && <div className="error-msg">{editError}</div>}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
```

- [ ] **Step 3: 重写 App.jsx（侧边栏 + hash 路由）**

```jsx
// web/src/App.jsx
import { useEffect, useState } from "react";
import { api } from "./api.js";
import { useRunStream } from "./hooks/useRunStream.js";
import Login from "./pages/Login.jsx";
import Register from "./pages/Register.jsx";
import Accounts from "./pages/Accounts.jsx";
import Nodes from "./pages/Nodes.jsx";
import Settings from "./pages/Settings.jsx";

const NAV = [
  {
    key: "register",
    label: "注册",
    icon: (
      <svg className="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <polygon points="6 3 20 12 6 21 6 3" />
      </svg>
    ),
  },
  {
    key: "accounts",
    label: "账号",
    icon: (
      <svg className="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
        <circle cx="9" cy="7" r="4" />
        <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
        <path d="M16 3.13a4 4 0 0 1 0 7.75" />
      </svg>
    ),
  },
  {
    key: "nodes",
    label: "节点",
    icon: (
      <svg className="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="2" y="2" width="20" height="8" rx="2" />
        <rect x="2" y="14" width="20" height="8" rx="2" />
        <line x1="6" y1="6" x2="6.01" y2="6" />
        <line x1="6" y1="18" x2="6.01" y2="18" />
      </svg>
    ),
  },
  {
    key: "settings",
    label: "设置",
    icon: (
      <svg className="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="12" r="3" />
        <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
      </svg>
    ),
  },
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
      <div className="center-screen">
        <div className="loading">加载中…</div>
      </div>
    );
  }

  if (!authed) {
    return <Login onOk={() => setAuthed(true)} />;
  }

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="sidebar-brand">
          claude-register
          <span className="brand-sub">管理面板</span>
        </div>
        {NAV.map((item) => (
          <button
            key={item.key}
            className={`nav-item${view === item.key ? " active" : ""}`}
            onClick={() => navigate(item.key)}
          >
            {item.icon}
            <span className="nav-label">{item.label}</span>
          </button>
        ))}
      </aside>
      <main className="content-area">
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
    </div>
  );
}
```

- [ ] **Step 4: 删除 Dashboard.jsx**

```bash
git rm web/src/pages/Dashboard.jsx
```

- [ ] **Step 5: 验证 vite 转换**

```bash
cd web && (bun run dev &) && sleep 3
curl -sf http://localhost:5173/src/App.jsx | head -3
curl -sf http://localhost:5173/src/pages/Register.jsx | head -3
curl -sf http://localhost:5173/src/pages/Accounts.jsx | head -3
curl -sf http://localhost:5173/ | grep -o '<div id="root">'
```
Expected: 全部成功输出，终端无 vite 报错。验证完停 dev server。

- [ ] **Step 6: Commit**

```bash
git add -A web/src
git commit -m "feat(web): 侧边栏布局 + hash 路由，Dashboard 拆为 注册/账号 两页"
```

---

### Task 5: 全量走查 + 收尾

**Files:**
- Modify: 走查中发现的问题文件（若有）

- [ ] **Step 1: 起 dev server 全量核对**

```bash
cd web && bun run dev
```

逐项核对（可用浏览器或让用户核对）：
1. 四个导航项切换正常，active 高亮；hash 直链 `#/nodes` 刷新后仍在节点页；未知 hash 落到注册页。
2. 注册页：开始注册（可不真跑）、运行历史点击出详情。
3. 账号页：编辑展开/收起、复制、重跑按钮在无运行时可点。
4. 节点页：添加节点出卡片、字段带标签、删除、测试按钮报错文案正常（无节点服务时应报连接失败）、保存后重新加载值不丢。
5. 设置页：三张分组卡、toggle 可切换、保存后密钥仍显示占位符。
6. 窗口缩窄 <720px：侧边栏变顶部横条、只显示图标。

- [ ] **Step 2: 发现问题就地修复并单独提交**

修复类提交信息用 `fix(web): <问题>`。

- [ ] **Step 3: 确认工作区干净**

```bash
git status --short
git log --oneline -6
```
Expected: 无未提交改动，任务提交齐全。
