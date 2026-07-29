import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";

const STATUS_LABEL = {
  running: "运行中",
  success: "成功",
  failed: "失败",
  needs_manual: "需人工介入",
};

function StatusBadge({ status }) {
  return (
    <span className={`badge badge-${status || "unknown"}`}>
      {STATUS_LABEL[status] || status || "未知"}
    </span>
  );
}

export default function Dashboard() {
  const [domain, setDomain] = useState("");
  const [email, setEmail] = useState("");
  const [activeRunId, setActiveRunId] = useState(null);
  const [activeStatus, setActiveStatus] = useState(null);
  const [logLines, setLogLines] = useState([]);
  const [startError, setStartError] = useState("");
  const [starting, setStarting] = useState(false);

  const [runs, setRuns] = useState([]);
  const [accounts, setAccounts] = useState([]);

  const [selectedRun, setSelectedRun] = useState(null); // full detail
  const [rerunError, setRerunError] = useState("");

  const logPanelRef = useRef(null);
  const esRef = useRef(null);

  function refreshLists() {
    api.listRuns().then(setRuns).catch(() => {});
    api.listAccounts().then(setAccounts).catch(() => {});
  }

  useEffect(() => {
    refreshLists();
    return () => {
      if (esRef.current) esRef.current.close();
    };
  }, []);

  useEffect(() => {
    if (logPanelRef.current) {
      logPanelRef.current.scrollTop = logPanelRef.current.scrollHeight;
    }
  }, [logLines]);

  function attachStream(runId) {
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
      refreshLists();
    });
    es.onerror = () => {
      es.close();
      esRef.current = null;
    };
  }

  async function startRun() {
    setStartError("");
    setStarting(true);
    try {
      const res = await api.startRun(email || undefined, domain || undefined);
      attachStream(res.run_id);
    } catch (err) {
      if (err.status === 409) {
        setStartError("已有任务在运行");
      } else {
        setStartError("启动失败，请重试");
      }
    } finally {
      setStarting(false);
    }
  }

  async function openRun(id) {
    try {
      const detail = await api.runDetail(id);
      setSelectedRun(detail);
    } catch {
      setSelectedRun(null);
    }
  }

  async function doRerun(acctEmail) {
    setRerunError("");
    try {
      const res = await api.rerun(acctEmail);
      attachStream(res.run_id);
    } catch (err) {
      if (err.status === 409) {
        setRerunError(`「${acctEmail}」重跑失败：已有任务在运行`);
      } else {
        setRerunError(`「${acctEmail}」重跑失败`);
      }
    }
  }

  return (
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
        {rerunError && <div className="error-msg">{rerunError}</div>}

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

      <section className="card">
        <h2 className="card-title">账号列表</h2>
        {accounts.length === 0 ? (
          <div className="empty-hint">暂无账号</div>
        ) : (
          <ul className="list">
            {accounts.map((a) => (
              <li key={a.email} className="list-row">
                <span className="list-main">
                  <span>{a.email}</span>
                  <span className="list-sub">{a.domain}</span>
                </span>
                <StatusBadge status={a.status} />
                <button
                  className="btn btn-small"
                  onClick={() => doRerun(a.email)}
                  disabled={activeStatus === "running"}
                >
                  重跑
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
