import { useEffect, useState } from "react";
import { Upload } from "lucide-react";
import { toast } from "sonner";
import { api } from "../api.js";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  AlertDialog, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";

export function OAuthImportDialog({ open, onOpenChange, onImported }) {
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [report, setReport] = useState(null);
  const [attempted, setAttempted] = useState(false);
  const [bindDefaultGroup, setBindDefaultGroup] = useState(false);

  useEffect(() => {
    if (!open) {
      setFile(null);
      setPreview(null);
      setError("");
      setReport(null);
      setAttempted(false);
    }
  }, [open]);

  async function selectFile(event) {
    const selected = event.target.files?.[0];
    setFile(null);
    setPreview(null);
    setReport(null);
    setError("");
    setAttempted(false);
    if (!selected) return;
    if (selected.size > 10 * 1024 * 1024) {
      setError("JSON 文件不能超过 10 MB");
      return;
    }
    setBusy("preview");
    try {
      const result = await api.sub2apiImportPreview(selected);
      setPreview(result);
      setFile(selected);
    } catch (e) {
      if (e?.status !== 401) setError(e.body?.detail || "文件校验失败，请检查网络后重新选择文件");
    } finally {
      setBusy("");
    }
  }

  async function doImport() {
    if (!file || !preview || busy || attempted) return;
    setBusy("import");
    setError("");
    setAttempted(true);
    try {
      const result = await api.sub2apiImport(file, bindDefaultGroup);
      setReport(result);
      if (result.created) onImported?.();
      if (result.failed) toast.error(`导入完成：成功 ${result.created} 个，失败 ${result.failed} 个`);
      else toast.success(`已向 Sub2API 导入 ${result.created} 个账号`);
    } catch (e) {
      if (e?.status !== 401) {
        setError(`${e.body?.detail || "导入请求未完成"}。请先在 Sub2API 核对账号列表，避免重复导入。`);
      }
    } finally {
      setBusy("");
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={(value) => !busy && onOpenChange(value)}>
      <AlertDialogContent className="max-h-[90dvh] max-w-2xl overflow-y-auto">
        <AlertDialogHeader>
          <AlertDialogTitle>上传 OAuth JSON</AlertDialogTitle>
          <AlertDialogDescription>
            选择 Claude OAuth 文件，校验后转换并导入系统设置中配置的 Sub2API。
            支持单账号、账号数组、accounts 数组，以及 Claude Code 的 claudeAiOauth 格式。
          </AlertDialogDescription>
        </AlertDialogHeader>
        <div className="space-y-4 text-sm">
          <div className="space-y-1.5">
            <Label htmlFor="oauth-json-file">JSON 文件</Label>
            <Input id="oauth-json-file" type="file" accept=".json,application/json"
              disabled={!!busy} onChange={selectFile} />
            <p className="text-xs text-muted-foreground">最大 10 MB，每次最多 1000 个账号，支持 UTF-8 BOM。</p>
          </div>
          {busy === "preview" && <p role="status">正在校验文件…</p>}
          {error && <p role="alert" className="text-red-400">{error}</p>}
          {preview && (
            <div className="space-y-2">
              <p>已识别 {preview.accounts.length} 个 Claude OAuth 账号</p>
              <div className="max-h-52 overflow-auto rounded-md border">
                <table className="w-full text-left text-xs">
                  <thead className="bg-muted">
                    <tr><th className="p-2">账号名称</th><th className="p-2">并发</th>
                      <th className="p-2">优先级</th><th className="p-2">刷新令牌</th></tr>
                  </thead>
                  <tbody>{preview.accounts.map((a, i) => (
                    <tr key={i} className="border-t">
                      <td className="max-w-60 truncate p-2" title={a.name}>{a.name}</td>
                      <td className="p-2">{a.concurrency}</td><td className="p-2">{a.priority}</td>
                      <td className="whitespace-nowrap p-2">{a.has_refresh_token ? "有" : "无"}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
              <p className="text-xs text-muted-foreground">
                导入名称、OAuth 凭据、并发和优先级；缺省并发为 1，优先级为 50。
                此操作在 Sub2API 新建账号，重复上传可能产生重复账号。
              </p>
              <label className="flex items-center gap-2">
                <input type="checkbox" className="size-4 accent-primary"
                  checked={bindDefaultGroup} disabled={!!busy || attempted}
                  onChange={(e) => setBindDefaultGroup(e.target.checked)} />
                绑定 Sub2API 默认分组
              </label>
              <p className="text-xs text-muted-foreground">
                分组和代理可在 Sub2API 中调整。缺少刷新令牌的账号到期后需重新授权。
              </p>
            </div>
          )}
          {report && (
            <div role="status" className="space-y-2 rounded-md border p-3">
              <p>导入完成：成功 {report.created} 个，失败 {report.failed} 个。</p>
              {report.failed > 0 && <>
                <p className="text-amber-400">请检查失败账号的凭据和配置，仅重新提交失败账号。</p>
                {report.failed_names.length > 0 && (
                  <ul className="max-h-32 list-inside list-disc overflow-auto break-all text-xs">
                    {report.failed_names.map((name, i) => <li key={i}>{name}</li>)}
                  </ul>
                )}
              </>}
            </div>
          )}
        </div>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={!!busy}>关闭</AlertDialogCancel>
          <Button onClick={doImport} disabled={!preview || !!busy || attempted}>
            <Upload /> {busy === "import" ? "导入中…" : "确认导入 Sub2API"}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
