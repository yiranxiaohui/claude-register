import { useState } from "react";
import { toast } from "sonner";
import { api } from "../api.js";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

export default function Login({ onOk }) {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    if (!password) return;
    setBusy(true);
    try {
      await api.login(password);
      onOk();
    } catch (err) {
      toast.error(err.status === 401 ? "密码错误" : "登录失败，请重试");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <Card className="w-80">
        <CardHeader className="text-center">
          <CardTitle className="text-xl">claude-register</CardTitle>
          <p className="text-sm text-muted-foreground">管理面板登录</p>
        </CardHeader>
        <CardContent>
          <form className="flex flex-col gap-3" onSubmit={submit}>
            <Input
              type="password"
              placeholder="面板密码"
              value={password}
              autoFocus
              onChange={(e) => setPassword(e.target.value)}
            />
            <Button type="submit" disabled={busy}>
              {busy ? "登录中…" : "登录"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
