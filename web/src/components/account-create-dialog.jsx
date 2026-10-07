import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Plus } from "lucide-react";
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

// [字段, 标签, 占位, 是否占满一行, 是否等宽字体]
export const ACCOUNT_FIELDS = [
  ["email", "邮箱", "user@example.com", false, false],
  ["display_name", "备注", "给账号起个名字", false, false],
  ["password", "密码", "登录密码", false, false],
  ["session_key", "sessionKey", "sk-ant-sid01-…", true, true],
  ["proxy", "代理", "socks5://user:pass@host:port", true, true],
  ["mail_base_url", "收件地址 (mailUrl)", "https://mail.example.com", true, true],
  ["mail_key", "收件 Key (mailKey)", "AnyMail 子 Key", true, true],
];

export const emptyAccountForm = () =>
  Object.fromEntries(ACCOUNT_FIELDS.map(([k]) => [k, ""]));

export function AccountFields({ form, setForm, disabled, idPrefix }) {
  return (
    <div className="grid grid-cols-2 gap-2.5">
      {ACCOUNT_FIELDS.map(([key, label, hint, wide, mono]) => (
        <div key={key} className={`flex min-w-0 flex-col gap-1 ${wide ? "col-span-2" : ""}`}>
          <Label
            htmlFor={`${idPrefix}-${key}`}
            className="text-[11px] uppercase tracking-wide text-muted-foreground"
          >
            {label}
            {key === "email" ? <span className="text-red-400">*</span> : null}
          </Label>
          <Input
            id={`${idPrefix}-${key}`}
            className={mono ? "font-mono text-xs" : ""}
            value={form[key] ?? ""}
            placeholder={hint}
            spellCheck={false}
            disabled={disabled}
            onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
          />
        </div>
      ))}
    </div>
  );
}

export function AccountCreateDialog({ open, onOpenChange, onCreated }) {
  const [form, setForm] = useState(emptyAccountForm);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (open) setForm(emptyAccountForm());
  }, [open]);

  async function doCreate() {
    if (!form.email.trim()) return toast.error("请填写邮箱");
    setBusy(true);
    try {
      const row = await api.accountCreate(form);
      toast.success(`已新增账号：${row.email}`);
      onOpenChange(false);
      onCreated?.(row);
    } catch (e) {
      if (e?.status === 401) return;
      toast.error(
        e.status === 409
          ? "该邮箱的账号已存在"
          : e.body?.detail || `新增失败（${e.status || "?"}）`,
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={(o) => !busy && onOpenChange(o)}>
      <AlertDialogContent className="max-w-lg">
        <AlertDialogHeader>
          <AlertDialogTitle>新增账号</AlertDialogTitle>
          <AlertDialogDescription>
            手动录入一个已有账号。只有邮箱必填，其余字段可以之后再编辑。
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AccountFields form={form} setForm={setForm} disabled={busy} idPrefix="create" />
        <AlertDialogFooter>
          <AlertDialogCancel disabled={busy}>取消</AlertDialogCancel>
          <Button onClick={doCreate} disabled={busy}>
            <Plus /> {busy ? "保存中…" : "新增"}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
