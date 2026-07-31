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
      <form className="settings-form" onSubmit={save}>
        {GROUPS.map((group) => (
          <section className="card settings-group-card" key={group.title}>
            <h2 className="card-title">{group.title}</h2>
            <div className="settings-form">
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
            </div>
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
