import { useState } from "react";
import { api } from "../api.js";

export default function Login({ onOk }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    if (!password) return;
    setBusy(true);
    setError("");
    try {
      await api.login(password);
      onOk();
    } catch (err) {
      setError(err.status === 401 ? "密码错误" : "登录失败，请重试");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="center-screen">
      <form className="login-card" onSubmit={submit}>
        <h1 className="login-title">claude-register</h1>
        <p className="login-sub">管理面板登录</p>
        <input
          type="password"
          className="input"
          placeholder="面板密码"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoFocus
        />
        {error && <div className="error-msg">{error}</div>}
        <button className="btn btn-primary" type="submit" disabled={busy}>
          {busy ? "登录中…" : "登录"}
        </button>
      </form>
    </div>
  );
}
