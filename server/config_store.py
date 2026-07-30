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


def load_config(path: Path) -> Config:
    if not Path(path).is_file():
        return Config()
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    panel = raw.get("panel", {}) or {}
    anymail = raw.get("anymail", {}) or {}
    reg = raw.get("register", {}) or {}
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
}


def save_config(path: Path, updates: dict) -> Config:
    cfg = load_config(path)
    # 密码/密钥留空 = 不修改
    clean = dict(updates)
    for secret in ("panel_password", "anymail_api_key"):
        if secret in clean and clean[secret] in ("", REDACTED, None):
            clean.pop(secret)
    cfg = replace(cfg, **{k: v for k, v in clean.items() if k in _FIELD_MAP})
    out: dict = {"panel": {}, "anymail": {}, "register": {}}
    for field, (section, key) in _FIELD_MAP.items():
        out[section][key] = getattr(cfg, field)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(yaml.safe_dump(out, allow_unicode=True, sort_keys=False),
                          encoding="utf-8")
    return cfg


def to_redacted_dict(cfg: Config) -> dict:
    d = {f: getattr(cfg, f) for f in _FIELD_MAP}
    for secret in ("panel_password", "anymail_api_key"):
        if d[secret]:
            d[secret] = REDACTED
    return d
