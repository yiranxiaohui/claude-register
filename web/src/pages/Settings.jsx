import { useEffect, useState } from "react";
import { toast } from "sonner";
import { api } from "../api.js";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
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

const GROUPS = [
  {
    title: "面板",
    fields: [
      { key: "panel_password", label: "面板密码（留空不修改）", type: "password" },
      { key: "panel_port", label: "面板端口", type: "number" },
    ],
  },
  {
    title: "AnyMail 邮箱",
    fields: [
      { key: "anymail_api_key", label: "AnyMail API Key", type: "text" },
      { key: "anymail_base_url", label: "AnyMail Base URL", type: "text" },
      { key: "anymail_domain", label: "AnyMail 域名", type: "text" },
      { key: "anymail_expires_hours", label: "邮箱有效期（小时，0=永久）", type: "number" },
    ],
  },
  {
    title: "注册参数",
    fields: [
      { key: "register_login_timeout", label: "登录超时（秒）", type: "number" },
      { key: "register_auto_login", label: "注册后自动登录", type: "checkbox" },
      { key: "register_code_regex", label: "验证码正则", type: "text" },
      {
        key: "register_proxy",
        label: "注册代理（留空直连）",
        type: "text",
        placeholder: "http://user:pass@host:port 或 socks5://host:port",
      },
    ],
  },
  {
    title: "开放 API",
    fields: [
      { key: "api_enabled", label: "启用开放 API（/api/v1/*）", type: "checkbox" },
      { key: "api_key", label: "API Key", type: "apikey" },
    ],
  },
];

function ApiKeyField({ value, onRotated }) {
  const [shown, setShown] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  async function rotate() {
    setBusy(true);
    try {
      const { api_key } = await api.rotateApiKey();
      onRotated(api_key);
      setShown(true);
      toast.success(value ? "已生成新 Key，旧 Key 立即失效" : "已生成 API Key");
    } catch (err) {
      if (err?.status !== 401) toast.error("生成失败，请重试");
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      toast.success("已复制");
    } catch {
      // 非 HTTPS 页面没有剪贴板权限：显示出来让用户手动复制
      setShown(true);
      toast.error("浏览器不允许自动复制，请手动选中复制");
    }
  }

  return (
    <div className="space-y-1.5">
      <Label htmlFor="api_key">API Key</Label>
      <div className="flex gap-2">
        <Input
          id="api_key"
          readOnly
          type={shown ? "text" : "password"}
          value={value || ""}
          placeholder="尚未生成"
          className="font-mono"
          onFocus={(e) => shown && e.target.select()}
        />
        {value && (
          <>
            <Button type="button" variant="outline" onClick={() => setShown((v) => !v)}>
              {shown ? "隐藏" : "显示"}
            </Button>
            <Button type="button" variant="outline" onClick={copy}>复制</Button>
          </>
        )}
        <Button
          type="button"
          variant="outline"
          disabled={busy}
          onClick={() => (value ? setConfirming(true) : rotate())}
        >
          {value ? "重新生成" : "生成"}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        调用时放在请求头 <code>Authorization: Bearer &lt;key&gt;</code> 或 <code>X-API-Key</code>。
        生成后立即生效，无需再点保存。
      </p>
      <AlertDialog open={confirming} onOpenChange={setConfirming}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>重新生成 API Key？</AlertDialogTitle>
            <AlertDialogDescription>
              旧 Key 会立即失效，正在使用它的脚本需要换成新 Key。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction onClick={rotate}>重新生成</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function ApiDocs() {
  const origin = window.location.origin;
  const links = [
    [`${origin}/api/v1/docs.md`, "接口文档（Markdown，可直接发给 AI）"],
    [`${origin}/api/v1/docs`, "在线调试（Swagger）"],
    [`${origin}/api/v1/openapi.json`, "OpenAPI 规范（导入 Postman / Apifox）"],
    [`${origin}/llms.txt`, "AI 入口（llms.txt）"],
  ];
  return (
    <div className="space-y-1 text-sm">
      <div className="text-muted-foreground">接口文档（免 Key 访问，可分享给调用方）：</div>
      <ul className="space-y-0.5">
        {links.map(([href, label]) => (
          <li key={href} className="flex flex-wrap gap-x-2">
            <a className="font-mono text-xs text-primary underline-offset-2 hover:underline break-all"
              href={href} target="_blank" rel="noopener noreferrer">{href}</a>
            <span className="text-xs text-muted-foreground">{label}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ApiUsage() {
  const base = `${window.location.origin}/api/v1`;
  const example = [
    `# 1. 触发注册（可选 body：{"email": "...", "proxy_id": "..."}）`,
    `curl -X POST -H "Authorization: Bearer $KEY" ${base}/register`,
    `# → {"run_id": 12, "status": "running"}`,
    ``,
    `# 2. 等结果（wait 最长 50 秒，未完成就重复调用），只取需要的字段`,
    `curl -H "Authorization: Bearer $KEY" \\`,
    `  "${base}/register/12?wait=50&fields=email,session_key,password"`,
    ``,
    `# 3. 批量导出（format: text/json/csv/line；可按 status/check_status/claimed/emails 过滤）`,
    `curl -H "Authorization: Bearer $KEY" \\`,
    `  "${base}/accounts/export?format=line&fields=email,session_key&status=success"`,
    ``,
    `# 4. 获取一个账号（每次一个，自动标记为「已获取」，不会重复发放；没有可用账号返回 404）`,
    `curl -X POST -H "Authorization: Bearer $KEY" \\`,
    `  "${base}/accounts/claim?fields=email,session_key,password"`,
  ].join("\n");
  return (
    <details className="text-sm">
      <summary className="cursor-pointer text-muted-foreground">调用示例</summary>
      <pre className="mt-2 overflow-x-auto rounded-md bg-muted p-3 text-xs leading-5">{example}</pre>
      <p className="mt-1 text-xs text-muted-foreground">
        可用字段：GET {base}/fields；可选代理：GET {base}/proxies。
      </p>
    </details>
  );
}

const OWN_KEYS = GROUPS.flatMap((g) => g.fields.map((f) => f.key));

export default function Settings({ onPasswordSet }) {
  const [form, setForm] = useState(null);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState("");

  useEffect(() => {
    api
      .getConfig()
      .then((cfg) => {
        const picked = {};
        for (const k of OWN_KEYS) picked[k] = cfg[k];
        // 密码不回填：避免明文展示，也让「留空=不修改」语义对得上。
        picked.panel_password = "";
        setForm(picked);
      })
      .catch((err) => {
        if (err?.status !== 401) setLoadError("加载配置失败");
      });
  }, []);

  function setField(key, value) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  async function save(e) {
    e.preventDefault();
    setSaving(true);
    const changedPassword = !!(form.panel_password || "").trim();
    try {
      const updated = await api.putConfig(form);
      const picked = {};
      for (const k of OWN_KEYS) picked[k] = updated[k];
      picked.panel_password = "";
      setForm(picked);
      if (changedPassword) {
        // 会话签名绑定密码，改密码 = 当前 cookie 立即失效。
        // 不提示的话，面板会变成处处报错的「僵尸页」。
        toast.success("密码已更新，请用新密码重新登录");
        onPasswordSet?.();
        return;
      }
      toast.success("已保存");
    } catch (err) {
      if (err?.status !== 401) toast.error("保存失败，请重试");
    } finally {
      setSaving(false);
    }
  }

  if (loadError) return <div className="text-sm text-destructive">{loadError}</div>;
  if (!form) return <div className="text-sm text-muted-foreground">加载中…</div>;

  return (
    <>
      <form onSubmit={save}>
        {GROUPS.map((group) => (
          <Card className="mb-5" key={group.title}>
            <CardHeader>
              <CardTitle>{group.title}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {group.fields.map((f) =>
                f.type === "apikey" ? (
                  <ApiKeyField
                    key={f.key}
                    value={form[f.key]}
                    onRotated={(k) => setField(f.key, k)}
                  />
                ) : f.type === "checkbox" ? (
                  <div className="flex items-center justify-between" key={f.key}>
                    <Label htmlFor={f.key}>{f.label}</Label>
                    <Switch
                      id={f.key}
                      checked={!!form[f.key]}
                      onCheckedChange={(v) => setField(f.key, v)}
                    />
                  </div>
                ) : (
                  <div className="space-y-1.5" key={f.key}>
                    <Label htmlFor={f.key}>{f.label}</Label>
                    <Input
                      id={f.key}
                      type={f.type}
                      placeholder={f.placeholder ?? ""}
                      value={form[f.key] ?? ""}
                      onChange={(e) =>
                        setField(
                          f.key,
                          f.type === "number"
                            ? e.target.value.replace(/[^0-9.]/g, "")
                            : e.target.value,
                        )
                      }
                    />
                  </div>
                ),
              )}
              {group.title === "开放 API" && (
                <>
                  <ApiDocs />
                  <ApiUsage />
                </>
              )}
            </CardContent>
          </Card>
        ))}
        <Button type="submit" disabled={saving}>
          {saving ? "保存中…" : "保存设置"}
        </Button>
      </form>
    </>
  );
}
