import { useEffect, useState } from "react";
import { api } from "../api.js";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";

const newId = () => (globalThis.crypto?.randomUUID?.() || `proxy-${Date.now()}-${Math.random().toString(36).slice(2)}`);
const blank = () => ({ id: newId(), name: "", url: "" });
export default function Proxies() {
  const [items, setItems] = useState([]); const [form, setForm] = useState(blank); const [loading, setLoading] = useState(true);
  useEffect(() => { api.getConfig().then(c => setItems(c.saved_proxies || [])).catch(e => { if (e?.status !== 401) toast.error("加载失败"); }).finally(() => setLoading(false)); }, []);
  const save = async (e) => { e.preventDefault(); if (!form.name.trim() || !form.url.trim()) return toast.error("请填写名称和地址");
    const next = [...items.filter(x => x.id !== form.id), {...form, name: form.name.trim(), url: form.url.trim()}];
    try { await api.putConfig({saved_proxies: next}); setItems(next); setForm(blank()); toast.success("已保存"); } catch (e) { toast.error(e.body?.detail || "代理地址无效"); }
  };
  const remove = async id => { const next = items.filter(x => x.id !== id); try { await api.putConfig({saved_proxies: next}); setItems(next); } catch { toast.error("删除失败"); } };
  return <Card><CardHeader><CardTitle>代理池</CardTitle></CardHeader><CardContent className="space-y-5">
    <form onSubmit={save} className="grid gap-2 sm:grid-cols-[1fr_2fr_auto]"><Input placeholder="名称" value={form.name} onChange={e=>setForm({...form,name:e.target.value})}/><Input placeholder="http://user:pass@host:port 或 socks5://host:port" value={form.url} onChange={e=>setForm({...form,url:e.target.value})}/><Button>{items.some(x=>x.id===form.id)?"更新":"添加"}</Button></form>
    {loading ? <p>加载中…</p> : items.length === 0 ? <p className="text-sm text-muted-foreground">暂无代理</p> : <ul className="space-y-2">{items.map(p=><li key={p.id} className="flex items-center justify-between gap-3 rounded border p-3"><span className="min-w-0"><b>{p.name}</b><code className="ml-3 block truncate text-xs text-muted-foreground">{p.url}</code></span><span className="flex gap-2"><Button size="sm" variant="outline" onClick={()=>setForm(p)}>编辑</Button><Button size="sm" variant="destructive" onClick={()=>remove(p.id)}>删除</Button></span></li>)}</ul>}
  </CardContent></Card>;
}
