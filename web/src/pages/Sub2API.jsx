import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { KeyRound, RefreshCw } from "lucide-react";
import { api } from "../api.js";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";

const STATUS = {
  active: ["正常", "bg-emerald-500/15 text-emerald-400"],
  error: ["异常", "bg-red-500/15 text-red-400"],
  disabled: ["已停用", "bg-muted text-muted-foreground"],
};

const FILTERS = [
  ["error", "需重新授权（异常）"],
  ["fixable", "异常且可修复"],
  ["all", "全部"],
  ["unmatched", "本地无对应账号"],
];

const LIVE = { alive: "有效", dead: "失效", blocked: "被拦截", error: "检测失败" };

function canFix(a) {
  return !!a.local?.has_session_key;
}

function matchFilter(a, filter) {
  switch (filter) {
    case "error":
      return a.status === "error";
    case "fixable":
      return a.status === "error" && canFix(a);
    case "unmatched":
      return !a.local;
    default:
      return true;
  }
}

function fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString();
}

function TokenExpiry({ iso }) {
  if (!iso) return null;
  const expired = new Date(iso).getTime() <= Date.now();
  return (
    <span className={expired ? "text-red-400" : ""} title={iso}>
      令牌{expired ? "已过期" : "过期"} {fmtTime(iso)}
    </span>
  );
}

export default function Sub2API({ navigate }) {
  const [data, setData] = useState(null); // {base_url, accounts}
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("error");
  const [selected, setSelected] = useState(() => new Set());
  const [busy, setBusy] = useState(() => new Set());
  const [batch, setBatch] = useState(null); // {done,total}

  async function load() {
    setLoading(true);
    setError("");
    try {
      setData(await api.sub2apiAccounts());
    } catch (e) {
      if (e?.status === 401) return;
      setError(e.body?.detail || `加载失败（${e.status || "?"}）`);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  const accounts = data?.accounts || [];
  const visible = useMemo(
    () => accounts.filter((a) => matchFilter(a, filter)),
    [accounts, filter],
  );
  const selectedFixable = visible.filter((a) => selected.has(a.id) && canFix(a));
  const errorCount = accounts.filter((a) => a.status === "error").length;

  const setBusyFlag = (id, on) =>
    setBusy((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });

  // 重新授权并写回 sub2api；成功返回 true。quiet（批量）时不弹成功提示，失败始终提示原因。
  async function reauth(a, { quiet = false } = {}) {
    setBusyFlag(a.id, true);
    try {
      const res = await api.sub2apiReauth(a.id);
      setData((d) =>
        d && {
          ...d,
          accounts: d.accounts.map((x) => (x.id === a.id ? res.account : x)),
        },
      );
      if (!quiet) toast.success(`「${a.name}」已重新授权并写回 sub2api`);
      return true;
    } catch (e) {
      if (e?.status === 401) return false;
      const msg = e.body?.detail || `失败（${e.status || "?"}）`;
      toast.error(`「${a.name}」${msg}`, { duration: 8000 });
      return false;
    } finally {
      setBusyFlag(a.id, false);
    }
  }

  async function reauthBatch(list) {
    const queue = [...list];
    const stat = { done: 0, total: list.length, ok: 0, failed: 0 };
    setBatch({ ...stat });
    const worker = async () => {
      while (queue.length) {
        const a = queue.shift();
        const ok = await reauth(a, { quiet: true });
        stat.done += 1;
        if (ok) stat.ok += 1;
        else stat.failed += 1;
        setBatch({ ...stat });
      }
    };
    await Promise.all([worker(), worker()]);
    setBatch(null);
    setSelected(new Set());
    if (stat.failed) toast.error(`完成：成功 ${stat.ok} 个，失败 ${stat.failed} 个`);
    else toast.success(`完成：${stat.ok} 个账号已重新授权并写回 sub2api`);
  }

  const toggle = (id) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const fixableVisible = visible.filter(canFix);
  const allSelected =
    fixableVisible.length > 0 && fixableVisible.every((a) => selected.has(a.id));
  const toggleAll = () =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (allSelected) fixableVisible.forEach((a) => next.delete(a.id));
      else fixableVisible.forEach((a) => next.add(a.id));
      return next;
    });

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <CardTitle>sub2api 同步</CardTitle>
        <Button variant="outline" size="sm" onClick={load} disabled={loading || !!batch}>
          <RefreshCw className={cn(loading && "animate-spin")} /> 刷新
        </Button>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-muted-foreground">
          列出 sub2api 里的 Claude OAuth 账号，按邮箱对应到本地账号。「重新授权」会用本地 sessionKey
          （经账号绑定的代理）获取新的 OAuth 令牌，并写回 sub2api 的同一个账号：只替换令牌，
          保留并发、分组、模型映射等配置，同时清除异常状态。
          {data?.base_url ? <span className="ml-1 font-mono">{data.base_url}</span> : null}
        </p>
        {error && (
          <Alert className="border-red-500/30 bg-red-500/10">
            <AlertDescription className="flex w-full items-center justify-between gap-2 text-red-400">
              <span>{error}</span>
              {/未配置/.test(error) && (
                <Button variant="outline" size="sm" onClick={() => navigate("settings")}>
                  去设置
                </Button>
              )}
            </AlertDescription>
          </Alert>
        )}
        {data && (
          <div className="flex flex-wrap items-center gap-2">
            <select
              className="h-9 rounded-md border bg-background px-2 text-sm"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            >
              {FILTERS.map(([v, label]) => (
                <option key={v} value={v}>{label}</option>
              ))}
            </select>
            <label className="flex h-9 cursor-pointer items-center gap-2 px-1 text-sm text-muted-foreground select-none">
              <input
                type="checkbox"
                className="size-4 accent-primary"
                checked={allSelected}
                disabled={fixableVisible.length === 0 || !!batch}
                onChange={toggleAll}
              />
              全选可修复
            </label>
            <span className="text-xs text-muted-foreground">
              共 {accounts.length} 个，异常 {errorCount} 个，当前显示 {visible.length} 个
            </span>
            {selectedFixable.length > 0 && (
              <Button size="sm" disabled={!!batch} onClick={() => reauthBatch(selectedFixable)}>
                <KeyRound />
                {batch
                  ? `处理中 ${batch.done}/${batch.total}…`
                  : `重新授权所选（${selectedFixable.length}）`}
              </Button>
            )}
          </div>
        )}
        {loading && !data ? (
          <div className="text-sm text-muted-foreground">加载中…</div>
        ) : data && visible.length === 0 ? (
          <div className="text-sm text-muted-foreground">
            {filter === "error" ? "sub2api 里没有异常的 Claude OAuth 账号" : "没有符合条件的账号"}
          </div>
        ) : (
          <ul className="flex flex-col gap-1.5">
            {visible.map((a) => {
              const st = STATUS[a.status] || [a.status || "未知", "bg-muted text-muted-foreground"];
              return (
                <li
                  key={a.id}
                  className={cn(
                    "flex items-center justify-between gap-2 rounded-lg border bg-background/50 px-3 py-2.5 text-sm",
                    selected.has(a.id) && "border-primary/60",
                  )}
                >
                  <input
                    type="checkbox"
                    className="size-4 shrink-0 accent-primary"
                    aria-label={`选择 ${a.name}`}
                    checked={selected.has(a.id)}
                    disabled={!canFix(a) || !!batch}
                    onChange={() => toggle(a.id)}
                  />
                  <span className="flex min-w-0 flex-1 flex-col gap-1 overflow-hidden">
                    <span className="flex items-center gap-2">
                      <span className="truncate" title={a.name}>{a.name}</span>
                      <span className="shrink-0 text-xs text-muted-foreground">#{a.id}</span>
                    </span>
                    <span className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
                      <Badge className={cn("rounded-full border-transparent", st[1])}>{st[0]}</Badge>
                      <TokenExpiry iso={a.token_expires_at} />
                      {a.local ? (
                        <span>
                          本地：{a.local.email === a.name ? "已对应" : a.local.email}
                          {a.local.check_status
                            ? ` · ${LIVE[a.local.check_status] || a.local.check_status}`
                            : ""}
                          {a.local.has_session_key ? "" : " · 无 sessionKey"}
                        </span>
                      ) : (
                        <span className="text-amber-400">
                          {a.email ? `本地没有 ${a.email}` : "无法识别邮箱"}
                        </span>
                      )}
                    </span>
                    {a.error_message ? (
                      <span className="truncate text-xs text-red-400/90" title={a.error_message}>
                        {a.error_message}
                      </span>
                    ) : null}
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    className="shrink-0"
                    disabled={!canFix(a) || busy.has(a.id) || !!batch}
                    title={canFix(a) ? "用本地 sessionKey 重新授权并写回 sub2api" : "本地没有可用的 sessionKey"}
                    onClick={() => reauth(a)}
                  >
                    {busy.has(a.id) ? "授权中…" : "重新授权"}
                  </Button>
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
