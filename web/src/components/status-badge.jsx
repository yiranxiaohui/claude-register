import { cn } from "@/lib/utils";

const STATUS = {
  running: { label: "运行中", cls: "bg-blue-500/15 text-blue-400" },
  success: { label: "成功", cls: "bg-emerald-500/15 text-emerald-400" },
  failed: { label: "失败", cls: "bg-red-500/15 text-red-400" },
  needs_manual: { label: "需人工介入", cls: "bg-amber-500/15 text-amber-400" },
};

export function StatusBadge({ status }) {
  const s = STATUS[status] || { label: status || "未知", cls: "bg-muted text-muted-foreground" };
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold whitespace-nowrap",
        s.cls,
      )}
    >
      <span
        className={cn(
          "size-1.5 rounded-full bg-current",
          status === "running" && "animate-pulse",
        )}
      />
      {s.label}
    </span>
  );
}
