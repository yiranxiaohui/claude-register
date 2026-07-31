import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { api } from "../api.js";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";

export default function Register({ runStream }) {
  const { activeRunId, activeStatus, logLines, attach, streamEpoch } = runStream;
  const [domain, setDomain] = useState("");
  const [email, setEmail] = useState("");
  const [starting, setStarting] = useState(false);
  const [runs, setRuns] = useState([]);
  const [selectedRun, setSelectedRun] = useState(null);
  const logPanelRef = useRef(null);

  useEffect(() => {
    api.listRuns().then(setRuns).catch(() => {});
  }, [streamEpoch]);

  useEffect(() => {
    const vp = logPanelRef.current?.closest("[data-slot='scroll-area-viewport']");
    if (vp) vp.scrollTop = vp.scrollHeight;
  }, [logLines]);

  async function startRun() {
    setStarting(true);
    try {
      const res = await api.startRun(email || undefined, domain || undefined);
      attach(res.run_id);
    } catch (err) {
      toast.error(err.status === 409 ? "已有任务在运行" : "启动失败，请重试");
    } finally {
      setStarting(false);
    }
  }

  async function openRun(id) {
    try {
      setSelectedRun(await api.runDetail(id));
    } catch {
      setSelectedRun(null);
    }
  }

  return (
    <>
      <div className="grid items-start gap-5 min-[1200px]:grid-cols-[5fr_7fr]">
        <Card>
          <CardHeader>
            <CardTitle>开始注册</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <Input
              placeholder="邮箱后缀 / domain（可选）"
              value={domain}
              onChange={(e) => setDomain(e.target.value)}
            />
            <Input
              placeholder="已有邮箱（可选，用于重跑该账号）"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
            <Button
              onClick={startRun}
              disabled={starting || activeStatus === "running"}
            >
              {starting ? "启动中…" : "开始注册"}
            </Button>

            {activeRunId && (
              <div className="pt-2">
                <div className="mb-2 flex items-center justify-between text-sm text-muted-foreground">
                  <span>
                    运行 <code className="font-mono text-xs">{activeRunId}</code>
                  </span>
                  <StatusBadge status={activeStatus} />
                </div>
                <ScrollArea className="log-panel h-64 rounded-lg border bg-black/40 text-muted-foreground">
                  <div className="p-3" ref={logPanelRef}>
                    {logLines.length === 0 ? (
                      <div className="text-muted-foreground/60">等待日志输出…</div>
                    ) : (
                      logLines.map((line, i) => <div key={i}>{line}</div>)
                    )}
                  </div>
                </ScrollArea>
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>运行历史</CardTitle>
          </CardHeader>
          <CardContent>
            {runs.length === 0 ? (
              <div className="text-sm text-muted-foreground">暂无运行记录</div>
            ) : (
              <ScrollArea className="max-h-80">
                <ul className="flex flex-col gap-1.5 pr-3">
                {runs.map((r) => (
                  <li
                    key={r.id}
                    onClick={() => openRun(r.id)}
                    className="flex cursor-pointer items-center justify-between gap-2 rounded-lg border bg-background/50 px-3 py-2.5 text-sm transition-colors hover:border-ring"
                  >
                    <span className="flex min-w-0 flex-col gap-0.5">
                      <span className="font-mono text-xs">{r.id}</span>
                      <span className="truncate text-xs text-muted-foreground">
                        {r.email || "（未指定邮箱）"} {r.domain ? `@${r.domain}` : ""}
                      </span>
                    </span>
                    <StatusBadge status={r.status} />
                  </li>
                ))}
                </ul>
              </ScrollArea>
            )}

            {selectedRun && (
              <div className="mt-4 border-t pt-4">
                <div className="mb-2 flex items-center justify-between">
                  <h3 className="text-sm font-semibold">
                    运行详情 <span className="font-mono text-xs">{selectedRun.id}</span>
                  </h3>
                  <Button variant="ghost" size="sm" onClick={() => setSelectedRun(null)}>
                    关闭
                  </Button>
                </div>
                <StatusBadge status={selectedRun.status} />
                <ScrollArea className="log-panel mt-2 max-h-80 rounded-lg border bg-black/40 text-muted-foreground">
                  <pre className="p-3">{selectedRun.log || "（无日志）"}</pre>
                </ScrollArea>
                {selectedRun.screenshots && selectedRun.screenshots.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-2">
                    {selectedRun.screenshots.map((name) => (
                      <a
                        key={name}
                        href={`/runs/${selectedRun.id}/${name}`}
                        target="_blank"
                        rel="noreferrer"
                      >
                        <img
                          className="block h-[90px] w-[140px] rounded-md border object-cover"
                          src={`/runs/${selectedRun.id}/${name}`}
                          alt={name}
                        />
                      </a>
                    ))}
                  </div>
                )}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </>
  );
}
