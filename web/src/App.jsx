import { useEffect, useState } from "react";
import { Play, Users, Server, Settings as SettingsIcon } from "lucide-react";
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

  const current = NAV.find((n) => n.key === view);
  return (
    <SidebarProvider>
      <Sidebar collapsible="icon">
        <SidebarHeader>
          <div className="px-2 py-1.5 font-bold tracking-wide group-data-[collapsible=icon]:hidden">
            claude-register
            <span className="block text-[11px] font-normal text-muted-foreground">
              管理面板
            </span>
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
          <span className="text-sm font-medium">{current?.label}</span>
        </header>
        <div className="min-w-0 max-w-5xl flex-1 px-8 py-6 max-md:px-4">
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
        </div>
      </SidebarInset>
      <Toaster position="top-right" />
    </SidebarProvider>
  );
}
