# claude-register 接入 AnyMail 子 key 委派设计

日期:2026-07-30
状态:已确认

## 背景与目标

AnyMail 已支持「用 key 建 key」:持 `keys:create` scope 的 API key 可通过 `POST /api/keys` 派生权限只收窄的子 key(见 any-mail 仓库 `docs/superpowers/specs/2026-07-30-key-delegation-design.md`)。

claude-register 目前全程用一把静态父 key(`ANYMAIL_API_KEY` / 面板 `anymail.api_key`)。目标:

1. **按次派生**:每次注册在邮箱建好后,用父 key 派生一把仅 `emails:read`、address 锁定该邮箱、有效期随邮箱的一次性子 key;接码轮询全部改用子 key,父 key 不再出现在业务请求里。
2. **随账号导出**:注册成功后子 key 明文随账号落盘/导出,分享账号时对方可用它自行收魔术链接登录,且只能读这一个邮箱。
3. **优雅降级**:父 key 无 `keys:create`(403)或子集越界(400)时警告并退回现状(父 key 轮询),导出的 mailKey 留空;**父 key 永不进导出**。

## 组件设计

### 1. `claude_register/anymail.py` — 客户端扩展

新增两个方法(沿用现有 httpx.Client 即用即建、`RuntimeError` 报错风格):

```python
@dataclass(frozen=True)
class ChildKey:
    id: str
    plaintext: str  # ak_...,仅创建响应可得

def create_child_key(
    self, *, email: str, expires_at: str | None,
    name_prefix: str = "claude-register",
) -> ChildKey | None: ...

def delete_key(self, key_id: str) -> None: ...
```

- `create_child_key`:`POST {base_url}/api/keys`,body:
  ```json
  {
    "name": "claude-register <email>",
    "scopes": ["emails:read"],
    "provider": "domain",
    "address": "<完整邮箱地址>",
    "expires_at": "<邮箱的 expires_at,邮箱永久则 null>"
  }
  ```
  - 2xx:返回 `ChildKey(id=key.id, plaintext=plaintext)`。
  - **403 / 400:降级**——`log()` 警告(指明原因:父 key 缺 `keys:create` / 子集越界如父 key 有期限而邮箱永久),返回 `None`。不抛异常。
  - 其他 >=400(5xx 等)与网络异常(httpx 抛错):同样降级——派生失败不值得中断注册。**统一语义:任何创建失败都警告并返回 `None`,不抛。**
- `delete_key`:`DELETE {base_url}/api/keys/{id}`;404 视为成功(幂等),其他 >=400 只警告不抛(回收失败不影响主流程,子 key 有过期兜底)。

### 2. `claude_register/flow.py` — 流程接线

`run()` 中 `prepare_mailbox(...)` 之后:

```python
child = client.create_child_key(email=mailbox.email, expires_at=mailbox.expires_at)
poll_client = (
    AnyMailClient(base_url=..., api_key=child.plaintext, domain=..., code_regex=...)
    if child else client
)
```

- `run_browser` 签名扩展:`run_browser(client, mailbox, since, *, poll_client, mail_key, ...)`
  - 轮询(`poll_magic_link` / `poll_code`)与 `_report_manual_fallback` 改用 `poll_client`;
  - `_capture` 构造 `AccountRecord` 时带上 `mail_key`(降级时空串)与 `mail_base_url=client.base_url`。
  - 兼容:`poll_client` 参数默认 `None` 时回落为 `client`,`mail_key` 默认 `""`——既有测试/调用不破。
- **失败回收**:`run()` 末尾,若 `child` 存在且 `account` 为 None(未拿到 sessionKey),用父 key `client.delete_key(child.id)`,日志注明「注册未成功,已撤销子 key」。成功则保留子 key(它属于账号交付物)。
- 回收放 `finally` 不合适(成功也会走),用普通条件分支即可;`run_browser` 抛异常的路径也要回收——包一层 `try/except` 后再抛。

### 3. `claude_register/accounts.py` — 导出扩展

- `AccountRecord` 新增字段:`mail_key: str = ""`、`mail_base_url: str = ""`。
- `to_dict()` 恒输出这两个键(空串也输出,消费端好判断)。
- `line_export()` 改 5 段:`email----password----sessionKey----proxy----mailKey`(降级时第 5 段空串,段数恒定)。

### 4. 安全边界

- 导出物(jsonl/txt/account.json)只含子 key 明文,子 key 权限 = 单邮箱 `emails:read` + 随邮箱过期。
- 父 key 只出现在:配置存储(现状)、建邮箱、派生/撤销子 key 的请求头。
- 降级路径不把任何 key 写入导出的 mailKey 字段。

## 测试(pytest,沿用现有 mock 风格)

- `tests/test_anymail_child_key.py`(新):
  - `create_child_key` 2xx → 返回 id+明文,请求体 scopes/address/expires_at 正确;
  - 403、400、5xx、网络异常 → 均返回 None(降级),不抛;
  - `delete_key` 200/404 静默、500 警告不抛。
- `tests/test_accounts.py`(扩展):`line_export` 5 段;`mail_key` 空串时第 5 段为空;`to_dict` 恒含两个新键。
- `tests/test_flow_config.py` 或新 flow 测试(扩展):派生成功 → 轮询走子 key 客户端;降级 → 走父 key、mail_key 空;失败路径 → `delete_key` 被调用,成功路径 → 未调用。

## 不做的事(YAGNI)

- 不动 server 面板 UI(mail_key 随账号数据自然可见)。
- 不做子 key 轮换、缓存复用、批量派生。
- 不给子 key 加 `accounts:write`(收链接只需 `emails:read`)。
- 不改 4 段行格式的历史数据。
