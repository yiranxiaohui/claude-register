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
