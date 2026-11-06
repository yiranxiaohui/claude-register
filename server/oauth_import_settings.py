"""上传账号的创建参数：只允许明确支持的配置，先校验整批再写入。"""
from __future__ import annotations

import math


def _text(value, label, *, limit=100, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise ValueError(f"{label}必须是{'可为空的' if empty else '非空'}字符串，最多 {limit} 个字符")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(f"{label}包含无效字符") from None
    return value.strip()


def _integer(value, label, minimum=0, maximum=2**31 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{label}必须是 {minimum}–{maximum} 的整数")
    return value


def _model(value, *, wildcard=False):
    model = _text(value, "模型名称", limit=200)
    if any(c.isspace() for c in model) or ("*" in model and not (
        wildcard and model.count("*") == 1 and model.endswith("*")
    )):
        raise ValueError("白名单和目标模型须为完整模型名称；来源模型仅支持末尾的 * 通配符")
    return model


def apply_settings(payload: dict, settings) -> dict:
    if not isinstance(settings, dict):
        raise ValueError("创建参数必须是对象")
    allowed = {"names", "notes", "concurrency", "priority", "rate_multiplier", "proxy_id",
               "group_ids", "expires_at", "auto_pause_on_expired", "model_mode", "models", "model_mapping"}
    if set(settings) - allowed:
        raise ValueError("包含不支持的创建参数")
    common = {}
    for key, label, minimum in (("concurrency", "并发数", 1), ("priority", "优先级", 0)):
        if key in settings:
            common[key] = _integer(settings[key], label, minimum)
    rate = settings.get("rate_multiplier", 1)
    if type(rate) not in (int, float) or not 0 <= rate <= 1000000 or not math.isfinite(rate):
        raise ValueError("计费倍率必须是 0–1000000 的有限数值")
    common["rate_multiplier"] = rate
    common["notes"] = _text(settings.get("notes", ""), "备注", limit=5000, empty=True)
    proxy = settings.get("proxy_id")
    common["proxy_id"] = None if proxy is None else _integer(proxy, "代理 ID", 1)
    groups = settings.get("group_ids", [])
    if not isinstance(groups, list) or len(groups) > 100:
        raise ValueError("分组必须是数组，最多 100 个")
    common["group_ids"] = list(dict.fromkeys(_integer(g, "分组 ID", 1) for g in groups))
    expiry = settings.get("expires_at")
    common["expires_at"] = None if expiry is None else _integer(expiry, "账号到期时间", 1, 253402300799)
    pause = settings.get("auto_pause_on_expired", True)
    if type(pause) is not bool:
        raise ValueError("到期自动暂停必须是布尔值")
    common["auto_pause_on_expired"] = pause
    mode = settings.get("model_mode", "keep")
    if mode not in ("keep", "all", "whitelist", "mapping"):
        raise ValueError("模型限制模式无效")
    mapping = {}
    if mode == "whitelist":
        models = settings.get("models")
        if not isinstance(models, list) or not 1 <= len(models) <= 200:
            raise ValueError("模型白名单需要 1–200 个完整模型名称")
        mapping = {_model(m): _model(m) for m in models}
    elif mode == "mapping":
        raw = settings.get("model_mapping")
        if not isinstance(raw, dict) or not 1 <= len(raw) <= 200:
            raise ValueError("模型映射需要 1–200 条规则")
        mapping = {_model(k, wildcard=True): _model(v) for k, v in raw.items()}
    names = settings.get("names", [a["name"] for a in payload["accounts"]])
    if not isinstance(names, list) or len(names) != len(payload["accounts"]):
        raise ValueError("账号名称数量必须与预览账号数量一致")
    accounts = []
    for index, (account, name) in enumerate(zip(payload["accounts"], names), 1):
        credentials = dict(account["credentials"])
        if mode == "all":
            credentials.pop("model_mapping", None)
        elif mode in ("whitelist", "mapping"):
            credentials["model_mapping"] = dict(mapping)
        accounts.append({**account, **common, "name": _text(name, f"第 {index} 个账号名称"),
                         "credentials": credentials})
    return {**payload, "accounts": accounts}
