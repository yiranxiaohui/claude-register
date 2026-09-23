import { useEffect, useRef, useState } from "react";
import { Play, Users, Server, Settings as SettingsIcon, Sparkles, LogOut } from "lucide-react";
import { toast } from "sonner";
import { api, SESSION_EXPIRED_EVENT } from "./api.js";
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
  // Toaster 只挂一个、放在所有分支之外：登录态切换时若跟着分支卸载/重挂，
  // 切换瞬间发出的提示（如「登录已失效」）会丢。
  return (
    <>
      <AppBody />
      <Toaster position="top-right" />
    </>
  );
}

function AppBody() {
  const [authed, setAuthed] = useState(null); // null=checking, false=need login, true=ok
  const authedRef = useRef(authed);
  authedRef.current = authed;
  // 引导态：服务端还没设面板密码，只能去设置页设密码，其余接口全 401。
  const [bootstrap, setBootstrap] = useState(false);
  const [view, setView] = useState(viewFromHash);
  const runStream = useRunStream();

  useEffect(() => {
    api
      .getConfig()
      .then((cfg) => {
        // 引导态下 /api/config 也返回 200，必须看 panel_password 才能区分
        // 「已登录」和「未设密码」，否则会直接进一个处处 401 的空面板。
        setBootstrap(!cfg.panel_password);
        setAuthed(true);
      })
      .catch(() => setAuthed(false));
  }, []);

  useEffect(() => {
    const onExpired = () => {
      // 会话失效（密码被改/cookie 过期）→ 立即回登录页，而不是停在空页面。
      // 副作用不放进 setState 更新函数（StrictMode 下会被调两次）；
      // 并发多个 401 时用固定 id 去重，只弹一条。
      if (authedRef.current === true) {
        toast.error("登录已失效，请重新登录", { id: "session-expired" });
      }
      setAuthed(false);
    };
    window.addEventListener(SESSION_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, onExpired);
  }, []);

  useEffect(() => {
    const onHash = () => setView(viewFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  function navigate(next) {
    window.location.hash = `#/${next}`;
  }

  async function logout() {
    try {
      await api.logout();
    } catch {
      /* 登出失败也要回登录页 */
    }
    setAuthed(false);
    setBootstrap(false);
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
      <Login
        onOk={() => {
          setBootstrap(false);
          setAuthed(true);
        }}
      />
    );
  }

  // 引导态：只展示设置页，引导用户先把面板密码设上。
  if (bootstrap) {
    return (
      <div className="app-content mx-auto min-w-0 max-w-3xl px-8 py-8 max-md:px-4">
        <div className="mb-5 rounded-lg border border-amber-500/40 bg-amber-500/10 p-4 text-sm">
          <p className="font-semibold">面板尚未设置密码</p>
          <p className="mt-1 text-muted-foreground">
            未设密码时任何人都能打开本页并接管面板，注册、账号、代理等功能均已锁定。
            请先设定「面板密码」并保存，保存后需用新密码登录。
          </p>
        </div>
        <Settings
          onPasswordSet={() => {
            // 密码一旦设定，旧的引导态会话立即失效 → 回登录页。
            setBootstrap(false);
            setAuthed(false);
          }}
        />
      </div>
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
          <div className="flex-1"><span className="text-sm font-semibold">{current?.label}</span><span className="ml-3 hidden text-xs text-muted-foreground sm:inline">{current?.description}</span></div>
          <button
            type="button"
            onClick={logout}
            className="flex items-center gap-1.5 rounded-md px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          >
            <LogOut className="size-3.5" /> 退出登录
          </button>
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
    </SidebarProvider>
  );
}
