import { useCallback, useState } from "react";
import { Building2, ChevronRight } from "lucide-react";
import { api } from "../api.js";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

/**
 * 授权前选择组织（工作空间）。
 *
 * pick(email, { preferred }) 先向后端取账号的组织列表：
 * - 只有一个组织：直接返回它的 uuid，不弹窗；
 * - 有多个：弹窗让用户选，返回所选 uuid；用户取消时返回 undefined。
 * 获取组织列表失败时抛出接口错误，由调用方提示。
 */
export function useOrgPicker() {
  const [state, setState] = useState(null); // {email, orgs, def, current, resolve}

  const pick = useCallback(async (email, { preferred } = {}) => {
    const res = await api.accountOAuthOrgs(email, preferred);
    const orgs = res.organizations || [];
    if (orgs.length <= 1) return orgs[0]?.uuid || null;
    return new Promise((resolve) => {
      setState({ email, orgs, def: res.default || "", current: res.current || "", resolve });
    });
  }, []);

  const finish = (value) => {
    state?.resolve(value);
    setState(null);
  };

  const dialog = <OrgSelectDialog state={state} onDone={finish} />;
  return { pick, dialog };
}

function OrgSelectDialog({ state, onDone }) {
  return (
    <AlertDialog open={!!state} onOpenChange={(o) => !o && onDone(undefined)}>
      <AlertDialogContent className="max-w-lg">
        <AlertDialogHeader>
          <AlertDialogTitle>选择组织（工作空间）</AlertDialogTitle>
          <AlertDialogDescription>
            「{state?.email}」属于多个组织，请选择要授权给 Claude Code 的组织。
            令牌的额度与权限取决于所选组织。
          </AlertDialogDescription>
        </AlertDialogHeader>
        <div className="flex flex-col gap-2">
          {state?.orgs.map((o) => (
            <button
              key={o.uuid}
              type="button"
              onClick={() => onDone(o.uuid)}
              className={cn(
                "group flex w-full items-center gap-3 rounded-lg border bg-muted/30 px-3.5 py-3 text-left",
                "transition-colors hover:border-ring hover:bg-muted/60 focus-visible:border-ring focus-visible:outline-none",
                o.uuid === state.def && "border-ring/60",
              )}
            >
              <Building2 className="size-4 shrink-0 text-muted-foreground" />
              <span className="flex min-w-0 flex-1 flex-col gap-1">
                <span className="flex flex-wrap items-center gap-1.5">
                  <span className="truncate font-medium">{o.name || "（未命名组织）"}</span>
                  {o.uuid === state.current ? (
                    <Badge className="rounded-full border-transparent bg-sky-500/15 text-sky-400">
                      上次授权
                    </Badge>
                  ) : o.uuid === state.def ? (
                    <Badge variant="secondary" className="rounded-full">
                      默认
                    </Badge>
                  ) : null}
                  {o.raven_type ? (
                    <Badge variant="outline" className="rounded-full font-normal">
                      {o.raven_type}
                    </Badge>
                  ) : null}
                </span>
                <span className="truncate font-mono text-xs text-muted-foreground" title={o.uuid}>
                  {o.uuid}
                  {o.rate_limit_tier ? ` · ${o.rate_limit_tier}` : ""}
                </span>
              </span>
              <ChevronRight className="size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
            </button>
          ))}
        </div>
        <AlertDialogFooter>
          <AlertDialogCancel>取消</AlertDialogCancel>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
