"""config.yaml 读写 + 密码脱敏。替代 .env。"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import yaml

REDACTED = "••••"


@dataclass(frozen=True)
class Config:
    panel_password: str = ""
    panel_port: int = 8790
    anymail_api_key: str = ""
    anymail_base_url: str = ""
    anymail_domain: str = ""
    anymail_expires_hours: float = 0.0  # <=0 表示永久（默认）
    register_login_timeout: float = 120.0
    register_auto_login: bool = True
    register_code_regex: str = ""
    register_proxy: str = ""
    xui_enabled: bool = False
    xui_expiry_days: int = 30
    xui_port_min: int = 40000
    xui_port_max: int = 60000
    xui_nodes: tuple = ()
    takeover_enabled: bool = True
    takeover_idle_timeout_min: int = 15


_NODE_KEYS = ("name", "base_url", "username", "password", "proxy_host")


def _load_node(raw: dict) -> dict:
    d = raw or {}
    return {k: str(d.get(k, "") or "") for k in _NODE_KEYS}


def load_config(path: Path) -> Config:
    if not Path(path).is_file():
        return Config()
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    panel = raw.get("panel", {}) or {}
    anymail = raw.get("anymail", {}) or {}
    reg = raw.get("register", {}) or {}
    xui = raw.get("xui", {}) or {}
    pr = xui.get("port_range") or [40000, 60000]
    nodes = tuple(_load_node(n) for n in (xui.get("nodes") or []))
    tk = raw.get("takeover", {}) or {}
    return Config(
        panel_password=str(panel.get("password", "") or ""),
        panel_port=int(panel.get("port", 8790)),
        anymail_api_key=str(anymail.get("api_key", "") or ""),
        anymail_base_url=str(anymail.get("base_url", "") or ""),
        anymail_domain=str(anymail.get("domain", "") or ""),
        anymail_expires_hours=float(anymail.get("expires_hours", 0.0)),
        register_login_timeout=float(reg.get("login_timeout", 120.0)),
        register_auto_login=bool(reg.get("auto_login", True)),
        register_code_regex=str(reg.get("code_regex", "") or ""),
        register_proxy=str(reg.get("proxy", "") or ""),
        xui_enabled=bool(xui.get("enabled", False)),
        xui_expiry_days=int(xui.get("expiry_days", 30)),
        xui_port_min=int(pr[0]),
        xui_port_max=int(pr[1]),
        xui_nodes=nodes,
        takeover_enabled=bool(tk.get("enabled", True)),
        takeover_idle_timeout_min=int(tk.get("idle_timeout_min", 15)),
    )


_FIELD_MAP = {
    "panel_password": ("panel", "password"),
    "panel_port": ("panel", "port"),
    "anymail_api_key": ("anymail", "api_key"),
    "anymail_base_url": ("anymail", "base_url"),
    "anymail_domain": ("anymail", "domain"),
    "anymail_expires_hours": ("anymail", "expires_hours"),
    "register_login_timeout": ("register", "login_timeout"),
    "register_auto_login": ("register", "auto_login"),
    "register_code_regex": ("register", "code_regex"),
    "register_proxy": ("register", "proxy"),
    "takeover_enabled": ("takeover", "enabled"),
    "takeover_idle_timeout_min": ("takeover", "idle_timeout_min"),
}


def save_config(path: Path, updates: dict) -> Config:
    cfg = load_config(path)
    # 密码/密钥留空 = 不修改
    clean = dict(updates)
    for secret in ("panel_password", "anymail_api_key"):
        if secret in clean and clean[secret] in ("", REDACTED, None):
            clean.pop(secret)
    # xui 标量：不在 _FIELD_MAP，手动并入
    xui_scalar = {}
    for k in ("xui_enabled", "xui_expiry_days", "xui_port_min", "xui_port_max"):
        if k in clean:
            xui_scalar[k] = clean.pop(k)
    incoming_nodes = clean.pop("xui_nodes", None)
    cfg = replace(cfg, **{k: v for k, v in clean.items() if k in _FIELD_MAP})
    cfg = replace(cfg, **xui_scalar)
    if incoming_nodes is not None:
        old_by_base = {n["base_url"]: n for n in cfg.xui_nodes}
        merged = []
        for raw_node in incoming_nodes:
            node = _load_node(raw_node)
            if node["password"] in ("", REDACTED):
                node["password"] = old_by_base.get(node["base_url"], {}).get("password", "")
            merged.append(node)
        cfg = replace(cfg, xui_nodes=tuple(merged))
    out: dict = {"panel": {}, "anymail": {}, "register": {}, "xui": {}, "takeover": {}}
    for field, (section, key) in _FIELD_MAP.items():
        out[section][key] = getattr(cfg, field)
    out["xui"] = {
        "enabled": cfg.xui_enabled,
        "expiry_days": cfg.xui_expiry_days,
        "port_range": [cfg.xui_port_min, cfg.xui_port_max],
        "nodes": [dict(n) for n in cfg.xui_nodes],
    }
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(yaml.safe_dump(out, allow_unicode=True, sort_keys=False),
                          encoding="utf-8")
    return cfg


def to_redacted_dict(cfg: Config) -> dict:
    d = {f: getattr(cfg, f) for f in _FIELD_MAP}
    for secret in ("panel_password", "anymail_api_key"):
        if d[secret]:
            d[secret] = REDACTED
    d["xui_enabled"] = cfg.xui_enabled
    d["xui_expiry_days"] = cfg.xui_expiry_days
    d["xui_port_min"] = cfg.xui_port_min
    d["xui_port_max"] = cfg.xui_port_max
    d["xui_nodes"] = [
        {**n, "password": REDACTED if n.get("password") else ""} for n in cfg.xui_nodes
    ]
    return d
