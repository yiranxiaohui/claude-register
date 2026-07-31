import { useEffect, useState } from "react";
import { Play, Users, Server, Settings as SettingsIcon } from "lucide-react";
import { api } from "./api.js";
import { useRunStream } from "./hooks/useRunStream.js";
import { cn } from "@/lib/utils";
import { Toaster } from "@/components/ui/sonner";
import Login from "./pages/Login.jsx";
import Register from "./pages/Register.jsx";
import Accounts from "./pages/Accounts.jsx";
import Nodes from "./pages/Nodes.jsx";
import Settings from "./pages/Settings.jsx";

const NAV = [
  { key: "register", label: "注册", Icon: Play },
  { key: "accounts", label: "账号", Icon: Users },
  { key: "nodes", label: "节点", Icon: Server },
  { key: "settings", label: "设置", Icon: SettingsIcon },
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
      <div className="flex min-h-screen items-center justify-center">
        <div className="text-muted-foreground">加载中…</div>
      </div>
    );
  }

  if (!authed) {
    return (
      <>
        <Login onOk={() => setAuthed(true)} />
        <Toaster position="top-right" />
      </>
    );
  }

  return (
    <div className="flex min-h-screen max-md:flex-col">
      <aside className="flex w-52 shrink-0 flex-col gap-1 border-r bg-card px-3 py-5 max-md:w-full max-md:flex-row max-md:items-center max-md:overflow-x-auto max-md:border-r-0 max-md:border-b max-md:px-3 max-md:py-2">
        <div className="px-3 pb-4 font-bold tracking-wide max-md:whitespace-nowrap max-md:pb-0">
          claude-register
          <span className="block text-[11px] font-normal text-muted-foreground max-md:hidden">
            管理面板
          </span>
        </div>
        {NAV.map(({ key, label, Icon }) => (
          <button
            key={key}
            onClick={() => navigate(key)}
            className={cn(
              "flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground",
              view === key && "bg-accent font-semibold text-foreground",
            )}
          >
            <Icon className="size-4 shrink-0" />
            <span className="max-md:hidden">{label}</span>
          </button>
        ))}
      </aside>
      <main className="min-w-0 max-w-5xl flex-1 px-8 py-7 max-md:px-4 max-md:py-4">
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
      <Toaster position="top-right" />
    </div>
  );
}
