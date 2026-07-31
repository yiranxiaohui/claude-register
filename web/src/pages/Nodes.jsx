import { useEffect, useState } from "react";
import { toast } from "sonner";
import { FlaskConical, Plus, Trash2, Eraser } from "lucide-react";
import { api } from "../api.js";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";

const XUI_KEYS = ["xui_enabled", "xui_expiry_days", "xui_port_min", "xui_port_max", "xui_nodes"];

const POOL_FIELDS = [
  { key: "xui_expiry_days", label: "代理有效期（天）" },
  { key: "xui_port_min", label: "端口范围下限" },
  { key: "xui_port_max", label: "端口范围上限" },
];

const NODE_FIELDS = [
  { key: "name", label: "名称", placeholder: "如 usa-4" },
  { key: "base_url", label: "面板地址", placeholder: "http://host:2053" },
  { key: "username", label: "用户名", placeholder: "admin" },
  { key: "password", label: "密码", placeholder: "••••", type: "password" },
  { key: "proxy_host", label: "代理出口主机", placeholder: "对外连接用的 host" },
];

const EMPTY_NODE = { name: "", base_url: "", username: "", password: "", proxy_host: "" };

export default function Nodes() {
  const [form, setForm] = useState(null);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState("");

  useEffect(() => {
    api
      .getConfig()
      .then((cfg) => {
        const picked = {};
        for (const k of XUI_KEYS) picked[k] = cfg[k];
        picked.xui_nodes = cfg.xui_nodes || [];
        setForm(picked);
      })
      .catch(() => setLoadError("加载配置失败"));
  }, []);

  function setField(key, value) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  function setNode(i, key, value) {
    const nodes = [...form.xui_nodes];
    nodes[i] = { ...nodes[i], [key]: value };
    setField("xui_nodes", nodes);
  }

  async function save() {
    setSaving(true);
    try {
      const updated = await api.putConfig(form);
      const picked = {};
      for (const k of XUI_KEYS) picked[k] = updated[k];
      picked.xui_nodes = updated.xui_nodes || [];
      setForm(picked);
      toast.success("已保存");
    } catch {
      toast.error("保存失败，请重试");
    } finally {
      setSaving(false);
    }
  }

  async function testNode(node) {
    try {
      const r = await api.xuiTest(node);
      toast.success(`节点连接成功，现有 ${r.inbound_count} 个 inbound`);
    } catch (e) {
      toast.error(`节点连接失败：${e.body?.detail || e.message}`);
    }
  }

  async function cleanupExpired() {
    try {
      const r = await api.xuiCleanup();
      toast.success(`已清理过期 inbound：共 ${r.total} 个`);
    } catch {
      toast.error("清理失败，请重试");
    }
  }

  if (loadError) return <div className="text-sm text-destructive">{loadError}</div>;
  if (!form) return <div className="text-sm text-muted-foreground">加载中…</div>;

  return (
    <>

      <Card className="mb-5">
        <CardHeader>
          <CardTitle>代理池设置</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center justify-between">
            <Label htmlFor="xui_enabled">启用 3x-ui 代理池</Label>
            <Switch
              id="xui_enabled"
              checked={!!form.xui_enabled}
              onCheckedChange={(v) => setField("xui_enabled", v)}
            />
          </div>
          {POOL_FIELDS.map((f) => (
            <div className="space-y-1.5" key={f.key}>
              <Label htmlFor={f.key}>{f.label}</Label>
              <Input
                id={f.key}
                type="number"
                value={form[f.key] ?? ""}
                onChange={(e) => setField(f.key, Number(e.target.value))}
              />
            </div>
          ))}
        </CardContent>
      </Card>

      <Card className="mb-5">
        <CardHeader className="flex-row items-center justify-between space-y-0">
          <CardTitle>节点列表</CardTitle>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setField("xui_nodes", [...form.xui_nodes, { ...EMPTY_NODE }])}
          >
            <Plus /> 添加节点
          </Button>
        </CardHeader>
        <CardContent className="space-y-3">
          {form.xui_nodes.length === 0 ? (
            <div className="text-sm text-muted-foreground">暂无节点，点右上角添加</div>
          ) : (
            form.xui_nodes.map((node, i) => (
              <div key={i} className="rounded-xl border bg-background/50 p-4">
                <div className="mb-3 flex items-center justify-between">
                  <span className="text-sm font-semibold">
                    {node.name || `节点 ${i + 1}`}
                  </span>
                  <span className="flex gap-1.5">
                    <Button variant="outline" size="sm" onClick={() => testNode(node)}>
                      <FlaskConical /> 测试
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      className="text-destructive hover:text-destructive"
                      onClick={() =>
                        setField("xui_nodes", form.xui_nodes.filter((_, j) => j !== i))
                      }
                    >
                      <Trash2 /> 删除
                    </Button>
                  </span>
                </div>
                <div className="grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-2.5">
                  {NODE_FIELDS.map((f) => (
                    <div className="flex flex-col gap-1" key={f.key}>
                      <Label className="text-xs text-muted-foreground">{f.label}</Label>
                      <Input
                        type={f.type || "text"}
                        placeholder={f.placeholder}
                        value={node[f.key] ?? ""}
                        onChange={(e) => setNode(i, f.key, e.target.value)}
                      />
                    </div>
                  ))}
                </div>
              </div>
            ))
          )}
          <Button variant="ghost" size="sm" onClick={cleanupExpired}>
            <Eraser /> 清理过期 inbound
          </Button>
        </CardContent>
      </Card>

      <Button onClick={save} disabled={saving}>
        {saving ? "保存中…" : "保存节点配置"}
      </Button>
    </>
  );
}
