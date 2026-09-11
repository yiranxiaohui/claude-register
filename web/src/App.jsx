import { useEffect, useState } from "react";
import { Play, Users, Server, Settings as SettingsIcon, Sparkles } from "lucide-react";
import { api } from "./api.js";
import { useRunStream } from "./hooks/useRunStream.js";
import { Toaster } from "@/components/ui/sonner";
import { Separator } from "@/components/ui/separator";
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarRail,
  SidebarTrigger,
} from "@/components/ui/sidebar";
import Login from "./pages/Login.jsx";
import Register from "./pages/Register.jsx";
import Accounts from "./pages/Accounts.jsx";
import Nodes from "./pages/Nodes.jsx";
import Proxies from "./pages/Proxies.jsx";
import Settings from "./pages/Settings.jsx";

const NAV = [
  { key: "register", label: "注册任务", description: "创建与监控自动化任务", Icon: Play },
  { key: "accounts", label: "账号管理", description: "查看账号状态与凭据", Icon: Users },
  { key: "proxies", label: "代理池", description: "管理网络出口", Icon: Server },
  { key: "nodes", label: "节点设置", description: "配置 3x-ui 节点", Icon: Server },
  { key: "settings", label: "系统设置", description: "调整面板与注册参数", Icon: SettingsIcon },
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
        <div className="app-shell flex min-h-screen items-center justify-center">
        <div className="loading-mark"><Sparkles className="size-5" /> 加载面板…</div>
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

  const current = NAV.find((n) => n.key === view);
  return (
    <SidebarProvider>
      <Sidebar collapsible="icon">
        <SidebarHeader>
          <div className="brand px-2 py-2 group-data-[collapsible=icon]:hidden">
            <div className="flex items-center gap-2"><span className="brand-mark"><Sparkles className="size-4" /></span><span>claude-register</span></div>
            <span className="brand-subtitle">自动化注册工作台</span>
          </div>
        </SidebarHeader>
        <SidebarContent>
          <SidebarGroup>
            <SidebarMenu>
              {NAV.map(({ key, label, Icon }) => (
                <SidebarMenuItem key={key}>
                  <SidebarMenuButton
                    isActive={view === key}
                    tooltip={label}
                    onClick={() => navigate(key)}
                  >
                    <Icon />
                    <span>{label}</span>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroup>
        </SidebarContent>
        <SidebarRail />
      </Sidebar>
      <SidebarInset>
        <header className="flex h-12 shrink-0 items-center gap-2 border-b px-4">
          <SidebarTrigger className="-ml-1" />
          <Separator orientation="vertical" className="mr-1 h-4" />
          <div><span className="text-sm font-semibold">{current?.label}</span><span className="ml-3 hidden text-xs text-muted-foreground sm:inline">{current?.description}</span></div>
        </header>
        <div className="app-content min-w-0 max-w-6xl flex-1 px-8 py-8 max-md:px-4">
          {view === "register" && <Register runStream={runStream} />}
          {view === "accounts" && (
            <Accounts
              attach={runStream.attach}
              running={runStream.activeStatus === "running"}
              navigate={navigate}
            />
          )}
          {view === "proxies" && <Proxies />}
          {view === "nodes" && <Nodes />}
          {view === "settings" && <Settings />}
        </div>
      </SidebarInset>
      <Toaster position="top-right" />
    </SidebarProvider>
  );
}
