import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function initialImportSettings() {
  return { concurrency: "", priority: "", rate: "1", notes: "", proxyId: "", groupIds: [],
    expiresAt: "", autoPause: true, modelMode: "keep", modelText: "" };
}

export function buildImportSettings(form, names) {
  if (names.some((name) => !name.trim() || name.trim().length > 100)) {
    throw new Error("每个账号名称须为 1–100 个字符");
  }
  const settings = { names: names.map((name) => name.trim()), notes: form.notes,
    proxy_id: form.proxyId ? Number(form.proxyId) : null, group_ids: form.groupIds,
    auto_pause_on_expired: form.autoPause, model_mode: form.modelMode };
  for (const [key, label, minimum] of [["concurrency", "并发数", 1], ["priority", "优先级", 0]]) {
    if (form[key] === "") continue;
    const value = Number(form[key]);
    if (!Number.isSafeInteger(value) || value < minimum || value > 2147483647) {
      throw new Error(`${label}须为 ${minimum}–2147483647 的整数`);
    }
    settings[key] = value;
  }
  const rate = Number(form.rate);
  if (form.rate === "" || !Number.isFinite(rate) || rate < 0 || rate > 1000000) {
    throw new Error("计费倍率须为 0–1000000 的数值");
  }
  settings.rate_multiplier = rate;
  settings.expires_at = form.expiresAt ? Math.floor(new Date(form.expiresAt).getTime() / 1000) : null;
  if (form.expiresAt && (!Number.isFinite(settings.expires_at) || settings.expires_at <= 0)) {
    throw new Error("请选择有效的账号到期时间");
  }
  const lines = form.modelText.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  if (form.modelMode === "whitelist") {
    if (!lines.length || lines.some((line) => /[\s*]/.test(line))) {
      throw new Error("白名单每行填写一个完整模型名称，不支持空格或通配符");
    }
    settings.models = lines;
  } else if (form.modelMode === "mapping") {
    if (!lines.length) throw new Error("请至少填写一条模型映射");
    const pairs = lines.map((line) => line.split(/=>|=/).map((part) => part.trim()));
    if (pairs.some((pair) => pair.length !== 2 || pair.some((part) => !part || /\s/.test(part))
      || !/^[^*]+\*?$|^\*$/.test(pair[0]) || pair[1].includes("*"))) {
      throw new Error("模型映射每行填写「源模型 = 目标模型」，仅源模型末尾可使用 *");
    }
    if (new Set(pairs.map(([from]) => from)).size !== pairs.length) throw new Error("模型映射的源模型不能重复");
    settings.model_mapping = Object.fromEntries(pairs);
  }
  return settings;
}

export function OAuthImportSettings({ value, onChange, options, loading, error, onReload, disabled }) {
  const set = (key, next) => onChange((previous) => ({ ...previous, [key]: next }));
  return (
    <fieldset disabled={disabled} className="min-w-0 space-y-3 rounded-md border p-3">
      <legend className="px-1 font-medium">账号创建参数（应用到本批全部账号）</legend>
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="space-y-1.5">
          <Label htmlFor="oauth-concurrency">并发数</Label>
          <Input id="oauth-concurrency" type="number" min="1" max="2147483647" placeholder="沿用文件，缺省 1"
            value={value.concurrency} onChange={(e) => set("concurrency", e.target.value)} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="oauth-priority">优先级</Label>
          <Input id="oauth-priority" type="number" min="0" max="2147483647" placeholder="沿用文件，缺省 50"
            value={value.priority} onChange={(e) => set("priority", e.target.value)} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="oauth-rate">计费倍率</Label>
          <Input id="oauth-rate" type="number" min="0" max="1000000" step="0.001"
            value={value.rate} onChange={(e) => set("rate", e.target.value)} />
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span>优先级数值越小越优先。并发和优先级留空时沿用文件。</span>
        <Button type="button" variant="outline" size="sm" onClick={() => onChange((v) => ({ ...v, concurrency: "10", priority: "1", rate: "1" }))}>
          使用 Sub2API 默认值
        </Button>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="oauth-notes">备注</Label>
        <Input id="oauth-notes" maxLength={5000} value={value.notes} placeholder="可选，应用到本批账号"
          onChange={(e) => set("notes", e.target.value)} />
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs text-muted-foreground">代理和分组来自已配置的 Sub2API</span>
        <Button type="button" variant="outline" size="sm" onClick={onReload} disabled={loading || disabled}>
          {loading ? "加载中…" : "刷新代理和分组"}
        </Button>
      </div>
      {error && <p role="alert" className="text-red-400">{error}</p>}
      <div className="space-y-1.5">
        <Label htmlFor="oauth-proxy">代理</Label>
        <select id="oauth-proxy" className="h-9 w-full rounded-md border bg-background px-2"
          value={value.proxyId} disabled={loading || !options} onChange={(e) => set("proxyId", e.target.value)}>
          <option value="">直连（不使用代理）</option>
          {options?.proxies.map((proxy) => <option key={proxy.id} value={proxy.id}>{proxy.name}</option>)}
        </select>
      </div>
      <div className="space-y-1.5">
        <span className="font-medium">分组（可多选）</span>
        <div className="flex max-h-28 flex-wrap gap-x-4 gap-y-2 overflow-auto rounded-md border p-2">
          {options?.groups.map((group) => (
            <label key={group.id} className="flex items-center gap-2">
              <input type="checkbox" className="size-4 accent-primary" checked={value.groupIds.includes(group.id)}
                disabled={loading} onChange={(e) => set("groupIds", e.target.checked
                  ? [...value.groupIds, group.id] : value.groupIds.filter((id) => id !== group.id))} />
              {group.name}
            </label>
          ))}
          {options?.groups.length === 0 && <span className="text-muted-foreground">暂无可用的 Anthropic 分组</span>}
        </div>
        <p className="text-xs text-muted-foreground">未选择时使用 Sub2API 的 Anthropic 默认分组（如果存在），与创建账号流程一致。</p>
      </div>
      <details className="space-y-3">
        <summary className="cursor-pointer font-medium">到期时间与模型限制</summary>
        <div className="space-y-1.5">
          <Label htmlFor="oauth-expiry">账号到期时间</Label>
          <Input id="oauth-expiry" type="datetime-local" value={value.expiresAt}
            onChange={(e) => set("expiresAt", e.target.value)} />
          <p className="text-xs text-muted-foreground">留空表示不设置账号到期时间，使用浏览器本地时区。此项独立于 OAuth 令牌过期时间。</p>
        </div>
        <label className="flex items-center gap-2">
          <input type="checkbox" className="size-4 accent-primary" checked={value.autoPause}
            onChange={(e) => set("autoPause", e.target.checked)} />到期自动暂停
        </label>
        <div className="space-y-1.5">
          <Label htmlFor="oauth-model-mode">模型限制</Label>
          <select id="oauth-model-mode" className="h-9 w-full rounded-md border bg-background px-2"
            value={value.modelMode} onChange={(e) => set("modelMode", e.target.value)}>
            <option value="keep">沿用文件中的模型设置</option>
            <option value="all">允许所有模型</option>
            <option value="whitelist">模型白名单</option>
            <option value="mapping">模型映射</option>
          </select>
        </div>
        {["whitelist", "mapping"].includes(value.modelMode) && (
          <div className="space-y-1.5">
            <Label htmlFor="oauth-model-text">{value.modelMode === "whitelist" ? "模型名称（每行一个）" : "模型映射（每行一条）"}</Label>
            <textarea id="oauth-model-text" className="h-24 w-full rounded-md border bg-background p-2 font-mono text-xs"
              value={value.modelText} onChange={(e) => set("modelText", e.target.value)} spellCheck={false}
              placeholder={value.modelMode === "whitelist" ? "claude-sonnet-4-5-20250929" : "claude-sonnet-* = claude-sonnet-4-5-20250929"} />
          </div>
        )}
      </details>
    </fieldset>
  );
}
