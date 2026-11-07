import { useEffect, useRef, useState } from "react";
import { Upload } from "lucide-react";
import { toast } from "sonner";
import { api, prepareOAuthUpload } from "../api.js";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { OAuthImportSettings, initialImportSettings, buildImportSettings, prefixedImportName } from "@/components/oauth-import-settings";
import {
  AlertDialog, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";

export function OAuthImportDialog({ open, onOpenChange, onImported }) {
  const [upload, setUpload] = useState(null);
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [report, setReport] = useState(null);
  const [attempted, setAttempted] = useState(false);
  const [form, setForm] = useState(initialImportSettings);
  const [names, setNames] = useState([]);
  const [options, setOptions] = useState(null);
  const [optionsLoading, setOptionsLoading] = useState(false);
  const [optionsError, setOptionsError] = useState("");
  const optionsRequest = useRef(0);

  async function loadOptions() {
    const request = ++optionsRequest.current;
    setOptionsLoading(true);
    setOptionsError("");
    setOptions(null);
    try {
      const result = await api.sub2apiImportOptions();
      if (request === optionsRequest.current) setOptions(result);
    } catch (e) {
      if (request === optionsRequest.current && e?.status !== 401) {
        setOptionsError(e.body?.detail || "代理和分组加载失败，请刷新重试");
      }
    } finally {
      if (request === optionsRequest.current) setOptionsLoading(false);
    }
  }

  useEffect(() => {
    if (!open) {
      setUpload(null);
      setPreview(null);
      setError("");
      setReport(null);
      setAttempted(false);
      setNames([]);
      setForm(initialImportSettings());
      setOptions(null);
      setOptionsError("");
      setOptionsLoading(false);
      return;
    }
    loadOptions();
    return () => { optionsRequest.current += 1; };
  }, [open]);

  async function selectFile(event) {
    const selected = Array.from(event.target.files || []);
    setUpload(null);
    setPreview(null);
    setReport(null);
    setError("");
    setAttempted(false);
    setNames([]);
    if (!selected.length) return;
    setBusy("preview");
    try {
      const prepared = await prepareOAuthUpload(selected);
      const result = await api.sub2apiImportPreview(prepared);
      setPreview(result);
      setNames(result.accounts.map((account) => account.name));
      setUpload(prepared);
    } catch (e) {
      if (e?.status !== 401) setError(e.body?.detail || (e.status ? "文件校验失败，请重新选择文件" : e.message));
    } finally {
      setBusy("");
    }
  }

  async function doImport() {
    if (!upload || !preview || busy || attempted || !options || optionsLoading) return;
    let settings;
    try {
      settings = buildImportSettings(form, names);
      if (settings.group_ids.some((id) => !options.groups.some((group) => group.id === id))
        || (settings.proxy_id !== null && !options.proxies.some((proxy) => proxy.id === settings.proxy_id))) {
        throw new Error("所选代理或分组已失效，请重新选择");
      }
    } catch (e) {
      setError(e.message);
      return;
    }
    setBusy("import");
    setError("");
    setAttempted(true);
    try {
      const result = await api.sub2apiImport(upload, settings);
      setReport(result);
      if (result.created) onImported?.();
      if (result.failed) toast.error(`导入完成：成功 ${result.created} 个，失败 ${result.failed} 个`);
      else toast.success(`已向 Sub2API 导入 ${result.created} 个账号`);
    } catch (e) {
      if (e?.status !== 401) {
        if (e.status === 400) {
          setAttempted(false);
          setError(e.body?.detail || "创建参数校验失败，请修改后重试");
        } else {
          setError(`${e.body?.detail || e.message || "导入请求未完成"}。请先在 Sub2API 核对账号列表，避免重复导入。`);
        }
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
            可同时选择多个 Claude OAuth JSON 文件，全部校验后合并导入系统设置中配置的 Sub2API。
            支持单账号、账号数组、accounts 数组，以及 Claude Code 的 claudeAiOauth 格式。
          </AlertDialogDescription>
        </AlertDialogHeader>
        <div className="space-y-4 text-sm">
          <div className="space-y-1.5">
            <Label htmlFor="oauth-json-file">JSON 文件（可多选）</Label>
            <Input id="oauth-json-file" type="file" accept=".json,application/json" multiple
              disabled={!!busy} onChange={selectFile} />
            <p className="text-xs text-muted-foreground">每批最多 1000 个文件、合计 1000 个账号，上传总大小最多 10 MB，支持 UTF-8 BOM。</p>
          </div>
          {busy === "preview" && <p role="status">正在校验文件…</p>}
          {error && <p role="alert" className="text-red-400">{error}</p>}
          <OAuthImportSettings value={form} onChange={setForm} options={options} loading={optionsLoading}
            error={optionsError} onReload={loadOptions} disabled={!!busy || attempted} />
          {preview && (
            <div className="space-y-2">
              <p>已选择 {preview.files?.length || 1} 个文件，已识别 {preview.accounts.length} 个 Claude OAuth 账号</p>
              <div className="max-h-52 overflow-auto rounded-md border">
                <table className="w-full text-left text-xs">
                  <thead className="bg-muted">
                    <tr><th className="p-2">账号名称</th><th className="p-2">并发</th>
                      <th className="p-2">优先级</th><th className="p-2">刷新令牌</th></tr>
                  </thead>
                  <tbody>{preview.accounts.map((a, i) => (
                    <tr key={i} className="border-t">
                      <td className="max-w-60 p-2">
                        <Input aria-label={`第 ${i + 1} 个账号名称`} className="min-w-32 text-xs" maxLength={100}
                          value={names[i] ?? a.name} disabled={!!busy || attempted}
                          onChange={(e) => setNames((previous) => previous.map((name, index) => index === i ? e.target.value : name))} />
                        {form.namePrefix.trim() && (
                          <div className="break-all text-muted-foreground">
                            导入名称：{prefixedImportName(names[i] ?? a.name, form.namePrefix)}
                          </div>
                        )}
                        {a.source_file && <div className="truncate text-muted-foreground" title={a.source_file}>{a.source_file}</div>}
                      </td>
                      <td className="p-2">{form.concurrency || a.concurrency}</td><td className="p-2">{form.priority || a.priority}</td>
                      <td className="whitespace-nowrap p-2">{a.has_refresh_token ? "有" : "无"}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
              <p className="text-xs text-muted-foreground">
                可在表格中修改各账号名称（不含统一前缀），上方参数应用到所有账号。
                此操作在 Sub2API 新建账号，重复上传可能产生重复账号。
              </p>
              <p className="text-xs text-muted-foreground">
                缺少刷新令牌的账号到期后需重新授权。
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
          <Button onClick={doImport} disabled={!preview || !!busy || attempted || !options || optionsLoading}>
            <Upload /> {busy === "import" ? "导入中…" : "确认导入 Sub2API"}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
