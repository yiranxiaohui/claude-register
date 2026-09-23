import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Download } from "lucide-react";
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

const STORAGE_KEY = "cr:export-options";

const FORMATS = [
  ["text", "文本块（每行「字段：值」）"],
  ["line", "单行（值用分隔符连接）"],
  ["csv", "CSV（Excel 可打开）"],
  ["json", "JSON"],
];

const SCOPES = [
  ["all", "全部账号", {}],
  ["success", "仅注册成功（有 sessionKey）", { status: "success" }],
  ["alive", "仅检测有效", { check_status: "alive" }],
];

const EXT = { text: "txt", line: "txt", csv: "csv", json: "json" };

function loadSaved() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
  } catch {
    return null;
  }
}

export function ExportDialog({ open, onOpenChange }) {
  const [meta, setMeta] = useState(null);
  const [fields, setFields] = useState([]);
  const [format, setFormat] = useState("text");
  const [sep, setSep] = useState("----");
  const [scope, setScope] = useState("all");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open || meta) return;
    api
      .exportFields()
      .then((d) => {
        setMeta(d);
        const saved = loadSaved();
        const known = new Set(d.fields.map((f) => f.key));
        const savedFields = (saved?.fields || []).filter((k) => known.has(k));
        setFields(savedFields.length ? savedFields : d.default_fields);
        if (saved?.format && EXT[saved.format]) setFormat(saved.format);
        if (saved?.sep) setSep(saved.sep);
        if (saved?.scope && SCOPES.some(([k]) => k === saved.scope)) setScope(saved.scope);
      })
      .catch((e) => {
        if (e?.status !== 401) toast.error("加载导出字段失败");
      });
  }, [open, meta]);

  function toggle(key) {
    setFields((prev) => {
      if (prev.includes(key)) return prev.filter((k) => k !== key);
      // 保持字段表里的顺序，导出列顺序可预期
      const order = meta.fields.map((f) => f.key);
      return [...prev, key].sort((a, b) => order.indexOf(a) - order.indexOf(b));
    });
  }

  async function doExport() {
    if (!fields.length) return toast.error("至少选择一个字段");
    if (format === "line" && !sep) return toast.error("请填写分隔符");
    setBusy(true);
    try {
      const params = { fields: fields.join(","), format, ...SCOPES.find(([k]) => k === scope)[2] };
      if (format === "line") params.sep = sep;
      const text = await api.exportAccountsText(params);
      if (!text.replace(/^\ufeff/, "").trim() || (format === "json" && text.trim() === "[]")) {
        toast.error("没有符合条件的账号");
        return;
      }
      const type = { json: "application/json", csv: "text/csv" }[format] || "text/plain";
      const url = URL.createObjectURL(new Blob([text], { type: `${type};charset=utf-8` }));
      const a = document.createElement("a");
      a.href = url;
      a.download = `accounts.${EXT[format]}`;
      a.click();
      URL.revokeObjectURL(url);
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ fields, format, sep, scope }));
      onOpenChange(false);
    } catch (e) {
      if (e?.status !== 401) toast.error("导出失败，请重试");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent className="max-w-lg">
        <AlertDialogHeader>
          <AlertDialogTitle>导出账号</AlertDialogTitle>
          <AlertDialogDescription>选择要导出的信息和格式。</AlertDialogDescription>
        </AlertDialogHeader>
        {!meta ? (
          <div className="text-sm text-muted-foreground">加载中…</div>
        ) : (
          <div className="space-y-4 text-sm">
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <Label>导出字段</Label>
                <span className="flex gap-3 text-xs">
                  <button type="button" className="text-muted-foreground hover:text-foreground"
                    onClick={() => setFields(meta.default_fields)}>默认</button>
                  <button type="button" className="text-muted-foreground hover:text-foreground"
                    onClick={() => setFields(meta.fields.map((f) => f.key))}>全选</button>
                </span>
              </div>
              <div className="grid grid-cols-2 gap-x-4 gap-y-2">
                {meta.fields.map((f) => (
                  <label key={f.key} className="flex cursor-pointer items-center gap-2">
                    <input
                      type="checkbox"
                      className="size-4 accent-primary"
                      checked={fields.includes(f.key)}
                      onChange={() => toggle(f.key)}
                    />
                    <span>{f.title}</span>
                    {f.secret && <span className="text-xs text-amber-500">敏感</span>}
                  </label>
                ))}
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <Label htmlFor="export-format">格式</Label>
                <select
                  id="export-format"
                  className="h-9 w-full rounded-md border bg-background px-2"
                  value={format}
                  onChange={(e) => setFormat(e.target.value)}
                >
                  {FORMATS.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
                </select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="export-scope">范围</Label>
                <select
                  id="export-scope"
                  className="h-9 w-full rounded-md border bg-background px-2"
                  value={scope}
                  onChange={(e) => setScope(e.target.value)}
                >
                  {SCOPES.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
                </select>
              </div>
            </div>
            {format === "line" && (
              <div className="space-y-1.5">
                <Label htmlFor="export-sep">分隔符</Label>
                <Input id="export-sep" value={sep} maxLength={16} onChange={(e) => setSep(e.target.value)} />
              </div>
            )}
          </div>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel>取消</AlertDialogCancel>
          <Button onClick={doExport} disabled={!meta || busy}>
            <Download /> {busy ? "导出中…" : "导出"}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
