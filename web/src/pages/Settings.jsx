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
  { key: "anymail_expires_hours", label: "邮箱有效期（小时）", type: "number" },
  { key: "register_login_timeout", label: "登录超时（秒）", type: "number" },
  { key: "register_auto_login", label: "注册后自动登录", type: "checkbox" },
  { key: "register_code_regex", label: "验证码正则", type: "text" },
  { key: "register_proxy", label: "注册代理（留空直连）", type: "text",
    placeholder: "http://user:pass@host:port 或 socks5://host:port" },
];

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
        {message && <div className="success-msg">{message}</div>}
        {error && <div className="error-msg">{error}</div>}
        <button className="btn btn-primary" type="submit" disabled={saving}>
          {saving ? "保存中…" : "保存设置"}
        </button>
      </form>
    </section>
  );
}
