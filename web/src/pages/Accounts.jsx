import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Download } from "lucide-react";
import { api } from "../api.js";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

const EDIT_FIELDS = [
  ["display_name", "备注", "给账号起个名字", false],
  ["password", "密码", "登录密码", false],
  ["session_key", "sessionKey", "sk-ant-sid01-…", true],
  ["proxy", "代理", "socks5://user:pass@host:port", true],
];

export default function Accounts({ attach, running, navigate }) {
  const [accounts, setAccounts] = useState([]);
  const [takeover, setTakeover] = useState({ running: false, email: null });
  const [copiedEmail, setCopiedEmail] = useState("");
  const [editingEmail, setEditingEmail] = useState("");
  const [editForm, setEditForm] = useState({});
  const [deleteTarget, setDeleteTarget] = useState("");

  function refreshLists() {
    api.listAccounts().then(setAccounts).catch(() => {});
    api.takeoverStatus().then(setTakeover).catch(() => {});
  }

  useEffect(() => {
    refreshLists();
  }, []);

  async function doRerun(acctEmail) {
    try {
      const res = await api.rerun(acctEmail);
      attach(res.run_id);
      toast.success("已开始重跑，正在跳转…");
      navigate("register");
    } catch (err) {
      toast.error(
        err.status === 409
          ? `「${acctEmail}」重跑失败：已有任务在运行`
          : `「${acctEmail}」重跑失败`,
      );
    }
  }

  async function copyLine(acct) {
    const text = acct.text || acct.email;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
      } else {
        // http 面板无 clipboard API，退回 execCommand
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      }
      setCopiedEmail(acct.email);
      setTimeout(() => setCopiedEmail(""), 1500);
    } catch {
      toast.error("复制失败，请手动复制");
    }
  }

  const startTakeover = async (acctEmail) => {
    try {
      await api.takeoverStart(acctEmail);
      setTakeover(await api.takeoverStatus());
    } catch (e) {
      toast.error(
        e.status === 409
          ? "已有接管会话，请先结束"
          : e.status === 400
            ? "该账号无 sessionKey"
            : e.status === 403
              ? "接管功能已禁用"
              : `启动接管失败（${e.status || "?"}）`,
      );
    }
  };

  const stopTakeover = async () => {
    try {
      await api.takeoverStop();
      setTakeover({ running: false, email: null });
    } catch {
      /* ignore */
    }
  };

  const startEdit = (a) => {
    setEditingEmail(a.email);
    setEditForm({
      display_name: a.display_name || "",
      password: a.password || "",
      session_key: a.session_key || "",
      proxy: a.proxy || "",
    });
  };

  const saveEdit = async () => {
    try {
      await api.accountUpdate(editingEmail, editForm);
      setEditingEmail("");
      refreshLists();
      toast.success("已保存");
    } catch (e) {
      toast.error(`保存失败（${e.status || "?"}）`);
    }
  };

  const confirmDelete = async () => {
    const email = deleteTarget;
    setDeleteTarget("");
    try {
      await api.accountDelete(email);
      setEditingEmail("");
      refreshLists();
      toast.success(`已删除 ${email}`);
    } catch (e) {
      toast.error(`删除失败（${e.status || "?"}）`);
    }
  };

  async function exportAll() {
    try {
      const text = await api.exportAccountsText();
      const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "accounts.txt";
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      toast.error("导出失败，请重试");
    }
  }

  return (
    <>
      <h1 className="mb-5 text-xl font-semibold">账号</h1>
      <Card>
        <CardHeader className="flex-row items-center justify-between space-y-0">
          <CardTitle>账号列表</CardTitle>
          {accounts.length > 0 && (
            <Button variant="outline" size="sm" onClick={exportAll}>
              <Download /> 导出全部
            </Button>
          )}
        </CardHeader>
        <CardContent className="space-y-3">
          {takeover.running && (
            <div className="flex items-center justify-between gap-2 rounded-lg bg-blue-500/15 px-3 py-2 text-sm text-blue-400">
              <span>正在接管：{takeover.email}</span>
              <span className="flex items-center gap-2">
                <Button variant="outline" size="sm" asChild>
                  <a
                    href="/vnc/?autoconnect=1&resize=scale"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    打开画面
                  </a>
                </Button>
                <Button variant="outline" size="sm" onClick={stopTakeover}>
                  结束接管
                </Button>
              </span>
            </div>
          )}
          {accounts.length === 0 ? (
            <div className="text-sm text-muted-foreground">暂无账号</div>
          ) : (
            <ul className="flex max-h-[560px] flex-col gap-1.5 overflow-x-hidden overflow-y-auto">
              {accounts.map((a) => (
                <li key={a.email} className="flex flex-col">
                  <div
                    className={`flex items-center justify-between gap-2 rounded-lg border bg-background/50 px-3 py-2.5 text-sm ${
                      editingEmail === a.email ? "rounded-b-none border-ring" : ""
                    }`}
                  >
                    <span className="flex min-w-0 flex-col gap-1 overflow-hidden">
                      <span className="truncate" title={a.email}>
                        {a.email}
                      </span>
                      <span className="flex items-center gap-2 overflow-hidden whitespace-nowrap text-xs text-muted-foreground">
                        <StatusBadge status={a.status} />
                        {a.display_name ? <span>{a.display_name}</span> : null}
                        {a.session_key ? (
                          <span className="font-mono">
                            sk {String(a.session_key).slice(7, 17)}…
                          </span>
                        ) : null}
                        {a.proxy ? (
                          <span className="rounded-full border px-1.5 text-[11px]">代理</span>
                        ) : null}
                      </span>
                    </span>
                    <span className="flex shrink-0 gap-1.5">
                      <Button variant="outline" size="sm" onClick={() => copyLine(a)}>
                        {copiedEmail === a.email ? "已复制" : "复制"}
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        className={editingEmail === a.email ? "border-ring text-foreground" : ""}
                        onClick={() =>
                          editingEmail === a.email ? setEditingEmail("") : startEdit(a)
                        }
                      >
                        {editingEmail === a.email ? "收起" : "编辑"}
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => doRerun(a.email)}
                        disabled={running}
                      >
                        重跑
                      </Button>
                      {a.session_key && (
                        <Button
                          variant="outline"
                          size="sm"
                          className="border-blue-500/50 text-blue-400 hover:text-blue-300"
                          onClick={() => startTakeover(a.email)}
                        >
                          接管
                        </Button>
                      )}
                    </span>
                  </div>
                  {editingEmail === a.email && (
                    <div className="flex flex-col gap-3 rounded-b-lg border border-t-0 border-ring bg-background/50 p-3.5">
                      <div className="grid grid-cols-2 gap-2.5">
                        {EDIT_FIELDS.map(([key, label, hint, wide]) => (
                          <div
                            key={key}
                            className={`flex min-w-0 flex-col gap-1 ${wide ? "col-span-2" : ""}`}
                          >
                            <Label className="text-[11px] uppercase tracking-wide text-muted-foreground">
                              {label}
                            </Label>
                            <Input
                              className={wide ? "font-mono text-xs" : ""}
                              value={editForm[key] ?? ""}
                              placeholder={hint}
                              spellCheck={false}
                              onChange={(e) =>
                                setEditForm((f) => ({ ...f, [key]: e.target.value }))
                              }
                            />
                          </div>
                        ))}
                      </div>
                      <div className="flex items-center gap-2">
                        <Button size="sm" onClick={saveEdit}>
                          保存
                        </Button>
                        <Button variant="outline" size="sm" onClick={() => setEditingEmail("")}>
                          取消
                        </Button>
                        <span className="flex-1" />
                        <Button
                          variant="destructive"
                          size="sm"
                          onClick={() => setDeleteTarget(a.email)}
                        >
                          删除账号
                        </Button>
                      </div>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <AlertDialog open={!!deleteTarget} onOpenChange={(o) => !o && setDeleteTarget("")}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除账号？</AlertDialogTitle>
            <AlertDialogDescription>
              确认删除账号 {deleteTarget}？此操作不可恢复。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-white hover:bg-destructive/90"
              onClick={confirmDelete}
            >
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
