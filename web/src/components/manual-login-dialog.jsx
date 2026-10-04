import { useEffect, useState } from "react";
import { toast } from "sonner";
import { LogIn } from "lucide-react";
import { api } from "../api.js";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

const STORAGE_KEY = "cr:manual-login-options";

function loadSaved() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || "null") || {};
  } catch {
    return {};
  }
}

export function ManualLoginDialog({ open, onOpenChange, onStarted }) {
  const [proxies, setProxies] = useState([]);
  const [proxyId, setProxyId] = useState("");
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    const saved = loadSaved();
    api
      .getConfig()
      .then((cfg) => {
        const list = cfg.saved_proxies || [];
        setProxies(list);
        setProxyId(list.some((p) => p.id === saved.proxyId) ? saved.proxyId : "");
      })
      .catch((e) => {
        if (e?.status !== 401) toast.error("代理列表加载失败");
      });
  }, [open]);

  async function doStart() {
    setBusy(true);
    try {
      await api.takeoverManual({
        email: email.trim() || undefined,
        proxy_id: proxyId || undefined,
      });
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ proxyId }));
      toast.success("登录浏览器已就绪，请点「打开画面」完成登录");
      setEmail("");
      onOpenChange(false);
      onStarted?.();
    } catch (e) {
      if (e?.status === 401) return;
      toast.error(
        e.status === 409
          ? "已有接管会话，请先结束"
          : e.status === 403
            ? "接管功能已禁用"
            : e.body?.detail || `启动失败（${e.status || "?"}）`,
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={(o) => !busy && onOpenChange(o)}>
      <AlertDialogContent className="max-w-lg">
        <AlertDialogHeader>
          <AlertDialogTitle>手动登录</AlertDialogTitle>
          <AlertDialogDescription>
            打开一个未登录的 claude.ai 浏览器，你在接管画面里自己登录任意邮箱（收码、过验证都手动完成）。
            登录成功后系统会自动取出 sessionKey 并新建账号；如果该邮箱已存在，则更新它的 sessionKey。
          </AlertDialogDescription>
        </AlertDialogHeader>
        <div className="space-y-4 text-sm">
          <div className="space-y-1.5">
            <Label htmlFor="manual-email">邮箱（可选）</Label>
            <Input
              id="manual-email"
              type="email"
              placeholder="user@example.com"
              value={email}
              disabled={busy}
              spellCheck={false}
              onChange={(e) => setEmail(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && !busy && doStart()}
            />
            <p className="text-xs text-muted-foreground">
              填写后会预填到登录框。账号最终以 claude.ai 返回的邮箱入库，查不到时才用这里填的邮箱。
            </p>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="manual-proxy">代理</Label>
            <select
              id="manual-proxy"
              className="h-9 w-full rounded-md border bg-background px-2"
              value={proxyId}
              disabled={busy}
              onChange={(e) => setProxyId(e.target.value)}
            >
              <option value="">不使用代理（直连，账号不绑定代理）</option>
              {proxies.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
            <p className="text-xs text-muted-foreground">
              浏览器经所选代理登录，取出的 sk 也会绑定该代理（之后检测 / 接管都走它）。
            </p>
          </div>
        </div>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={busy}>取消</AlertDialogCancel>
          <Button onClick={doStart} disabled={busy}>
            <LogIn /> {busy ? "正在启动浏览器…" : "开始"}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
