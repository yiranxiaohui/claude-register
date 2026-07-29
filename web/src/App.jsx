import { useEffect, useState } from "react";
import { api } from "./api.js";
import Login from "./pages/Login.jsx";
import Dashboard from "./pages/Dashboard.jsx";
import Settings from "./pages/Settings.jsx";

export default function App() {
  const [authed, setAuthed] = useState(null); // null=checking, false=need login, true=ok
  const [view, setView] = useState("dashboard"); // dashboard | settings

  useEffect(() => {
    api
      .getConfig()
      .then(() => setAuthed(true))
      .catch(() => setAuthed(false));
  }, []);

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
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">claude-register 管理面板</div>
        <nav className="tabs">
          <button
            className={view === "dashboard" ? "tab active" : "tab"}
            onClick={() => setView("dashboard")}
          >
            主面板
          </button>
          <button
            className={view === "settings" ? "tab active" : "tab"}
            onClick={() => setView("settings")}
          >
            设置
          </button>
        </nav>
      </header>
      <main className="main-content">
        {view === "dashboard" ? <Dashboard /> : <Settings />}
      </main>
    </div>
  );
}
