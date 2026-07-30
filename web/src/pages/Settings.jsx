import { useEffect, useState } from "react";
import { api } from "../api.js";

const SECRET_PLACEHOLDER = "••••";
const SECRET_FIELDS = ["panel_password", "anymail_api_key"];

const FIELD_DEFS = [
  { key: "panel_password", label: "面板密码", type: "password", secret: true },
  { key: "panel_port", label: "面板端口", type: "number" },
  { key: "anymail_api_key", label: "AnyMail API Key", type: "password", secret: true },
  { key: "anymail_base_url", label: "AnyMail Base URL", type: "text" },
  { key: "anymail_domain", label: "AnyMail 域名", type: "text" },
  { key: "anymail_expires_hours", label: "邮箱有效期（小时，0=永久）", type: "number" },
  { key: "register_login_timeout", label: "登录超时（秒）", type: "number" },
  { key: "register_auto_login", label: "注册后自动登录", type: "checkbox" },
  { key: "register_code_regex", label: "验证码正则", type: "text" },
  { key: "register_proxy", label: "注册代理（留空直连）", type: "text",
    placeholder: "http://user:pass@host:port 或 socks5://host:port" },
];

const XUI_FIELDS = [
  { key: "xui_enabled", label: "启用 3x-ui 代理池", type: "checkbox" },
  { key: "xui_expiry_days", label: "代理有效期（天）", type: "number" },
  { key: "xui_port_min", label: "端口范围下限", type: "number" },
  { key: "xui_port_max", label: "端口范围上限", type: "number" },
];

const EMPTY_NODE = { name: "", base_url: "", username: "", password: "", proxy_host: "" };

export default function Settings() {
  const [form, setForm] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .getConfig()
      .then((cfg) => {
        setForm(cfg);
        setLoading(false);
      })
      .catch(() => {
        setError("加载配置失败");
        setLoading(false);
      });
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
      setForm(updated);
      setMessage("已保存");
    } catch {
      setError("保存失败，请重试");
    } finally {
      setSaving(false);
    }
  }

  async function testNode(node) {
    setMessage(""); setError("");
    try {
      const r = await api.xuiTest(node);
      setMessage(`节点连接成功，现有 ${r.inbound_count} 个 inbound`);
    } catch (e) {
      setError(`节点连接失败：${e.body?.detail || e.message}`);
    }
  }

  async function cleanupExpired() {
    setMessage(""); setError("");
    try {
      const r = await api.xuiCleanup();
      setMessage(`已清理过期 inbound：共 ${r.total} 个`);
    } catch {
      setError("清理失败，请重试");
    }
  }

  if (loading) return <div className="empty-hint">加载中…</div>;
  if (!form) return <div className="error-msg">{error || "加载失败"}</div>;

  return (
    <section className="card settings-card">
      <h2 className="card-title">设置</h2>
      <form className="settings-form" onSubmit={save}>
        {FIELD_DEFS.map((f) => (
          <div className="form-field" key={f.key}>
            <label className="field-label" htmlFor={f.key}>
              {f.label}
            </label>
            {f.type === "checkbox" ? (
              <input
                id={f.key}
                type="checkbox"
                checked={!!form[f.key]}
                onChange={(e) => setField(f.key, e.target.checked)}
              />
            ) : (
              <input
                id={f.key}
                className="input"
                type={f.type}
                placeholder={f.secret ? SECRET_PLACEHOLDER : f.placeholder ?? ""}
                value={form[f.key] ?? ""}
                onChange={(e) =>
                  setField(
                    f.key,
                    f.type === "number" ? e.target.value.replace(/[^0-9.]/g, "") : e.target.value,
                  )
                }
              />
            )}
          </div>
        ))}

        <fieldset className="settings-group">
          <legend>3x-ui 代理池</legend>
          {XUI_FIELDS.map((f) => (
            <div className="form-field" key={f.key}>
              <label className="field-label" htmlFor={f.key}>{f.label}</label>
              {f.type === "checkbox" ? (
                <input id={f.key} type="checkbox"
                  checked={!!form[f.key]}
                  onChange={(e) => setField(f.key, e.target.checked)} />
              ) : (
                <input id={f.key} className="input" type="number"
                  value={form[f.key] ?? ""}
                  onChange={(e) => setField(f.key, Number(e.target.value))} />
              )}
            </div>
          ))}

          <div className="nodes-table">
            {(form.xui_nodes || []).map((node, i) => (
              <div className="node-row" key={i}>
                {["name", "base_url", "username", "password", "proxy_host"].map((k) => (
                  <input key={k} className="input" placeholder={k}
                    type={k === "password" ? "password" : "text"}
                    value={node[k] ?? ""}
                    onChange={(e) => {
                      const nodes = [...form.xui_nodes];
                      nodes[i] = { ...nodes[i], [k]: e.target.value };
                      setField("xui_nodes", nodes);
                    }} />
                ))}
                <button type="button" className="btn"
                  onClick={() => testNode(node)}>测试</button>
                <button type="button" className="btn btn-danger"
                  onClick={() => {
                    setField("xui_nodes",
                      form.xui_nodes.filter((_, j) => j !== i));
                  }}>删除</button>
              </div>
            ))}
            <button type="button" className="btn"
              onClick={() => setField("xui_nodes",
                [...(form.xui_nodes || []), { ...EMPTY_NODE }])}>
              + 添加节点
            </button>
            <button type="button" className="btn"
              onClick={cleanupExpired}>清理过期 inbound</button>
          </div>
        </fieldset>

        {message && <div className="success-msg">{message}</div>}
        {error && <div className="error-msg">{error}</div>}
        <button className="btn btn-primary" type="submit" disabled={saving}>
          {saving ? "保存中…" : "保存设置"}
        </button>
      </form>
    </section>
  );
}
