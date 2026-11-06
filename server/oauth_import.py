"""将上传的 Claude OAuth JSON 转为 Sub2API 数据导入格式。"""
from __future__ import annotations

import json
from datetime import datetime, timezone

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_ACCOUNTS = 1000
MAX_FILES = 1000
BATCH_CONTENT_TYPE = "application/vnd.claude-register.oauth-files+json"


def _invalid_constant(_value):
    raise ValueError("JSON 不能包含 NaN 或 Infinity")


def _load_json(content: bytes):
    if len(content) > MAX_FILE_BYTES:
        raise ValueError("JSON 文件不能超过 10 MB")
    try:
        return json.loads(content.decode("utf-8-sig"), parse_constant=_invalid_constant)
    except UnicodeDecodeError:
        raise ValueError("请上传 UTF-8 编码的 JSON 文件") from None
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 格式错误（第 {exc.lineno} 行，第 {exc.colno} 列）") from None
    except RecursionError:
        raise ValueError("JSON 嵌套层级过深") from None


def parse_file(content: bytes, filename: str = "claude-oauth.json") -> dict:
    source = _load_json(content)
    # 浏览器文件名仅用于缺省账号名称，不作为服务器路径使用。
    name = filename.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
    payload = convert(source, default_name=name.strip() or "claude-oauth")
    try:
        json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("JSON 包含无效的 Unicode 字符") from None
    except ValueError:
        raise ValueError("JSON 凭据包含无效数值") from None
    except RecursionError:
        raise ValueError("JSON 嵌套层级过深") from None
    return payload


def parse_files(content: bytes) -> tuple[dict, list[dict]]:
    """多个原始 JSON 文件先全部校验，再合并为一次 Sub2API 导入。"""
    batch = _load_json(content)
    files = batch.get("files") if isinstance(batch, dict) else None
    if not isinstance(files, list) or not files:
        raise ValueError("请选择至少一个 JSON 文件")
    if len(files) > MAX_FILES:
        raise ValueError(f"每次最多上传 {MAX_FILES} 个文件")
    combined = None
    summaries = []
    for index, file in enumerate(files, 1):
        if not isinstance(file, dict) or not isinstance(file.get("filename"), str):
            raise ValueError(f"第 {index} 个文件名无效")
        filename = file["filename"].replace("\\", "/").rsplit("/", 1)[-1]
        if not filename.strip() or len(filename) > 255:
            raise ValueError(f"第 {index} 个文件名无效")
        if not isinstance(file.get("content"), str):
            raise ValueError(f"第 {index} 个文件内容必须是 JSON 文本")
        try:
            filename.encode("utf-8")
            payload = parse_file(file["content"].encode("utf-8"), filename)
        except UnicodeEncodeError:
            raise ValueError(f"第 {index} 个文件包含无效的 Unicode 字符") from None
        except ValueError as exc:
            raise ValueError(f"文件「{filename}」：{exc}") from None
        summaries.append({"name": filename, "account_count": len(payload["accounts"])})
        if combined is None:
            combined = payload
        else:
            combined["accounts"].extend(payload["accounts"])
        if len(combined["accounts"]) > MAX_ACCOUNTS:
            raise ValueError(f"所有文件合计最多导入 {MAX_ACCOUNTS} 个账号，请分批上传")
    return combined, summaries


def convert(source, default_name="claude-oauth") -> dict:
    if isinstance(source, list):
        entries = source
    elif isinstance(source, dict):
        entries = source.get("accounts", [source])
    else:
        raise ValueError("输入必须是账号对象、账号数组或含 accounts 数组的对象")
    if not isinstance(entries, list) or not entries:
        raise ValueError("账号数组不能为空")
    if len(entries) > MAX_ACCOUNTS:
        raise ValueError(f"每次最多导入 {MAX_ACCOUNTS} 个账号")

    accounts = []
    for index, entry in enumerate(entries, 1):
        prefix = f"第 {index} 个账号"
        if not isinstance(entry, dict):
            raise ValueError(f"{prefix}必须是对象")
        credentials = entry.get("credentials")
        if credentials is None and "claudeAiOauth" in entry:
            oauth = entry["claudeAiOauth"]
            if not isinstance(oauth, dict):
                raise ValueError(f"{prefix}的 claudeAiOauth 必须是对象")
            credentials = {}
            for original, target in (
                ("accessToken", "access_token"),
                ("refreshToken", "refresh_token"),
                ("subscriptionType", "subscription_type"),
                ("rateLimitTier", "rate_limit_tier"),
            ):
                if original in oauth:
                    credentials[target] = oauth[original]
            if "expiresAt" in oauth:
                try:
                    if isinstance(oauth["expiresAt"], bool):
                        raise ValueError
                    expires_at = int(oauth["expiresAt"])
                except (TypeError, ValueError, OverflowError):
                    raise ValueError(f"{prefix}的 expiresAt 必须是时间戳") from None
                credentials["expires_at"] = str(expires_at // 1000)
            if "scopes" in oauth:
                scopes = oauth["scopes"]
                if isinstance(scopes, list) and all(isinstance(s, str) for s in scopes):
                    credentials["scope"] = " ".join(scopes)
                elif isinstance(scopes, str):
                    credentials["scope"] = scopes
                else:
                    raise ValueError(f"{prefix}的 scopes 必须是字符串数组或字符串")
            credentials["token_type"] = "Bearer"
        if not isinstance(credentials, dict) or not isinstance(credentials.get("access_token"), str) \
                or not credentials["access_token"].strip():
            raise ValueError(f"{prefix}缺少有效的 credentials.access_token")
        if entry.get("platform", "anthropic") != "anthropic" or entry.get("type", "oauth") != "oauth":
            raise ValueError(f"{prefix}不是 Claude OAuth 账号")
        name = (entry.get("name") or entry.get("email") or credentials.get("email_address")
                or (default_name if len(entries) == 1 else f"{default_name}-{index}"))
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{prefix}名称无效")
        account = {
            "name": name.strip(), "platform": "anthropic", "type": "oauth",
            "credentials": dict(credentials),
        }
        for key, default in (("concurrency", 1), ("priority", 50)):
            value = entry.get(key, default)
            if type(value) is not int or value < 0 or value > 2**63 - 1:
                raise ValueError(f"{prefix}的 {key} 必须是非负整数（不超过 2^63-1）")
            account[key] = value
        accounts.append(account)
    return {
        "type": "sub2api-data", "version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "proxies": [], "accounts": accounts,
    }
