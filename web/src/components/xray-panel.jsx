import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { FlaskConical, Play, RotateCw, Square } from "lucide-react";
import { api } from "../api.js";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const STATE_LABELS = {
  running: "运行中",
  stopped: "已停止",
  starting: "启动中",
  not_configured: "未配置",
  unavailable: "不可用",
  unknown: "状态未知",
};

const STATE_VARIANTS = {
  running: "default",
  stopped: "secondary",
  not_configured: "outline",
  unavailable: "destructive",
  unknown: "outline",
};

export function XrayPanel({ onConfigChanged }) {
  const [status, setStatus] = useState(null);
  const [proxy, setProxy] = useState("");
  const [busy, setBusy] = useState("");
  const [configured, setConfigured] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const next = await api.xrayStatus();
      setStatus(next);
      setConfigured(next.configured);
    } catch (err) {
      if (err?.status !== 401) toast.error("读取 Xray 状态失败");
    }
  }, []);

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 10000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  async function runAction(action) {
    setBusy(action);
    try {
      const next = await api.xrayAction(action);
      setStatus(next);
      const cfg = await api.getConfig();
      onConfigChanged?.(cfg);
      toast.success(`Xray${action === "start" ? "已启动" : action === "stop" ? "已停止" : "已重启"}`);
    } catch (err) {
      toast.error(err.body?.detail || "Xray 操作失败");
    } finally {
      setBusy("");
      refresh();
    }
  }

  async function configure() {
    setBusy("configure");
    try {
      const next = await api.xrayConfigure(proxy.trim());
      setStatus(next);
      const cfg = await api.getConfig();
      onConfigChanged?.(cfg);
      setConfigured(true);
      setProxy("");
      toast.success("Xray 上游已保存并启动，注册代理已切换到本地 Xray");
    } catch (err) {
      toast.error(err.body?.detail || "保存 Xray 上游失败");
    } finally {
      setBusy("");
      refresh();
    }
  }

  async function testEgress() {
    setBusy("test");
    try {
      const result = await api.xrayTest();
      toast.success(`Xray 出口正常：${result.ip}`);
    } catch (err) {
      toast.error(err.body?.detail || "Xray 出口测试失败");
    } finally {
      setBusy("");
    }
  }

  const state = status?.state || "unknown";
  const disabled = !!busy || !status?.available;

  return (
    <Card className="mb-5">
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <CardTitle>嵌入式 Xray</CardTitle>
        <Badge variant={STATE_VARIANTS[state] || "outline"}>
          {STATE_LABELS[state] || state}
        </Badge>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-sm text-muted-foreground">
          <span>注册流量：{status?.active ? "经 Xray" : "未使用 Xray"}</span>
          <span>SOCKS5：127.0.0.1:10808</span>
          {status?.pid && <span>PID：{status.pid}</span>}
          {status?.uptime && <span>运行时间：{status.uptime}</span>}
          {status?.message && <span>{status.message}</span>}
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="xray-upstream">SOCKS5 上游代理</Label>
          <Input
            id="xray-upstream"
            type="password"
            autoComplete="new-password"
            placeholder={configured ? "已保存，留空不修改" : "socks5://user:pass@host:port"}
            value={proxy}
            onChange={(event) => setProxy(event.target.value)}
          />
          <p className="text-xs text-muted-foreground">
            支持 socks5://host:port 和带认证地址。已保存凭据不会回显；配置成功后注册流量使用本地 Xray。
          </p>
        </div>

        <div className="flex flex-wrap gap-2">
          <Button onClick={configure} disabled={!proxy.trim() || !!busy || !status?.available}>
            保存上游并启动
          </Button>
          {state === "running" ? (
            <>
              <Button variant="outline" onClick={testEgress} disabled={disabled}>
                <FlaskConical /> 测试出口
              </Button>
              <Button variant="outline" onClick={() => runAction("restart")} disabled={disabled} title="重启 Xray">
                <RotateCw />
              </Button>
              <Button variant="outline" onClick={() => runAction("stop")} disabled={disabled}>
                <Square /> 停止并切回上游
              </Button>
            </>
          ) : (
            <Button variant="outline" onClick={() => runAction("start")} disabled={disabled || !status?.configured} title="启动 Xray">
              <Play /> 启动 Xray
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
