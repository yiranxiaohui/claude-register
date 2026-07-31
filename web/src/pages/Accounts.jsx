import { useEffect, useState } from "react";
import { api } from "../api.js";
import { StatusBadge } from "./Register.jsx";

const LIVE_LABEL = { alive: "有效", dead: "失效", error: "检测失败" };

function relTime(iso) {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const sec = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (sec < 60) return "刚刚";
  if (sec < 3600) return `${Math.floor(sec / 60)} 分钟前`;
  if (sec < 86400) return `${Math.floor(sec / 3600)} 小时前`;
  return `${Math.floor(sec / 86400)} 天前`;
}

function LiveBadge({ status, checkedAt, detail }) {
  if (!status) return null;
  return (
    <span className={`badge live-${status}`} title={detail || ""}>
      {LIVE_LABEL[status] || status}
      {checkedAt ? <span className="live-time"> · {relTime(checkedAt)}</span> : null}
    </span>
  );
}

export default function Accounts({ attach, running, navigate }) {
  const [accounts, setAccounts] = useState([]);
  const [takeover, setTakeover] = useState({ running: false, email: null });
  const [takeoverError, setTakeoverError] = useState("");
  const [rerunError, setRerunError] = useState("");
  const [checking, setChecking] = useState("");
  const [checkError, setCheckError] = useState("");
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

  async function doCheck(acctEmail) {
    setCheckError("");
    setChecking(acctEmail);
    try {
      const res = await api.checkAccount(acctEmail);
      setAccounts((list) =>
        list.map((a) =>
          a.email === acctEmail
            ? { ...a, check_status: res.status, checked_at: res.checked_at }
            : a,
        ),
      );
    } catch (e) {
      setCheckError(`「${acctEmail}」检测失败（${e.status || "?"}）`);
    } finally {
      setChecking("");
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
        {checkError && <div className="error-msg">{checkError}</div>}
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
                      <LiveBadge status={a.check_status} checkedAt={a.checked_at} />
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
                        className="btn btn-small"
                        onClick={() => doCheck(a.email)}
                        disabled={checking === a.email}
                      >
                        {checking === a.email ? "检测中…" : "检测"}
                      </button>
                    )}
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
