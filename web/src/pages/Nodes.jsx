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
          <button type="button" className="btn btn-ghost" onClick={cleanupExpired}>
            清理过期 inbound
          </button>
        </div>
      </section>

      {message && <div className="success-msg">{message}</div>}
      {error && <div className="error-msg">{error}</div>}
      <button className="btn btn-primary" onClick={save} disabled={saving} style={{ marginTop: 14 }}>
        {saving ? "保存中…" : "保存节点配置"}
      </button>
    </>
  );
}
