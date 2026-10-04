import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Upload } from "lucide-react";
import { api } from "../api.js";
import { Button } from "@/components/ui/button";
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

const STORAGE_KEY = "cr:import-options";

const RESULT_LABEL = {
  created: ["新增", "text-emerald-400"],
  updated: ["已更新", "text-blue-400"],
  skipped: ["跳过（失效）", "text-amber-400"],
  invalid: ["无法识别", "text-red-400"],
  duplicate: ["重复", "text-muted-foreground"],
};

function loadSaved() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || "null") || {};
  } catch {
    return {};
  }
}

export function ImportDialog({ open, onOpenChange, onImported }) {
  const [proxies, setProxies] = useState([]);
  const [proxyId, setProxyId] = useState("");
  const [check, setCheck] = useState(true);
  const [skipDead, setSkipDead] = useState(true);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState(null);

  useEffect(() => {
    if (!open) return;
    setReport(null);
    const saved = loadSaved();
    if (typeof saved.check === "boolean") setCheck(saved.check);
    if (typeof saved.skipDead === "boolean") setSkipDead(saved.skipDead);
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

  async function doImport() {
    if (!text.trim()) return toast.error("请粘贴要导入的 sessionKey");
    setBusy(true);
    try {
      const res = await api.importAccounts({
        text,
        proxy_id: proxyId || undefined,
        check,
        skip_dead: skipDead,
      });
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ proxyId, check, skipDead }));
      setReport(res);
      const s = res.summary;
      if (s.created + s.updated > 0) {
        toast.success(`导入完成：新增 ${s.created}，更新 ${s.updated}`);
        setText("");
        onImported?.();
      } else {
        toast.error("没有导入任何账号，请查看明细");
      }
    } catch (e) {
      if (e?.status !== 401) toast.error(e.body?.detail || `导入失败（${e.status || "?"}）`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={(o) => !busy && onOpenChange(o)}>
      <AlertDialogContent className="max-w-2xl">
        <AlertDialogHeader>
          <AlertDialogTitle>导入 sessionKey</AlertDialogTitle>
          <AlertDialogDescription>
            每行一个账号：<code>sk-ant-…</code>，或 <code>邮箱----sk-ant-…</code>（分隔符不限）。
            只有 sk 时会在检测时向 claude.ai 查询邮箱。
          </AlertDialogDescription>
        </AlertDialogHeader>
        <div className="space-y-4 text-sm">
          <textarea
            className="h-40 w-full resize-y rounded-md border bg-background p-2 font-mono text-xs"
            placeholder={"sk-ant-sid01-xxxx\nuser@example.com----sk-ant-sid01-yyyy"}
            spellCheck={false}
            value={text}
            disabled={busy}
            onChange={(e) => setText(e.target.value)}
          />
          <div className="space-y-1.5">
            <Label htmlFor="import-proxy">代理</Label>
            <select
              id="import-proxy"
              className="h-9 w-full rounded-md border bg-background px-2"
              value={proxyId}
              disabled={busy}
              onChange={(e) => setProxyId(e.target.value)}
            >
              <option value="">不使用代理（直连检测，账号不绑定代理）</option>
              {proxies.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
            <p className="text-xs text-muted-foreground">
              所选代理会用于检测，并写入账号的代理字段（之后检测 / 接管都走它）。在
              <a className="underline" href="#/proxies">代理池</a>中管理代理。
            </p>
          </div>
          <div className="flex flex-wrap gap-x-6 gap-y-2">
            <label className="flex cursor-pointer items-center gap-2">
              <input type="checkbox" className="size-4 accent-primary" checked={check}
                disabled={busy} onChange={(e) => setCheck(e.target.checked)} />
              导入前检测有效性并查询邮箱
            </label>
            <label className="flex cursor-pointer items-center gap-2">
              <input type="checkbox" className="size-4 accent-primary" checked={skipDead}
                disabled={busy || !check} onChange={(e) => setSkipDead(e.target.checked)} />
              跳过已失效的 sk
            </label>
          </div>
          {report && (
            <div className="max-h-48 overflow-auto rounded-md border bg-background/50 p-2 text-xs">
              {report.results.map((r) => {
                const [label, cls] = RESULT_LABEL[r.result] || [r.result, ""];
                return (
                  <div key={r.line} className="flex gap-2 py-0.5">
                    <span className="w-10 shrink-0 text-muted-foreground">#{r.line}</span>
                    <span className={`w-20 shrink-0 ${cls}`}>{label}</span>
                    <span className="min-w-0 truncate" title={r.detail || ""}>
                      {r.email || r.sk || ""}
                      {r.detail ? <span className="text-muted-foreground"> · {r.detail}</span> : null}
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </div>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={busy}>关闭</AlertDialogCancel>
          <Button onClick={doImport} disabled={busy}>
            <Upload /> {busy ? (check ? "检测并导入中…" : "导入中…") : "导入"}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
