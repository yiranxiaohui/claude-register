# claude-register 开放 API 文档

> 本文档可被 AI / 脚本直接读取：`GET {{BASE_URL}}/api/v1/docs.md`（Markdown）、
> `GET {{BASE_URL}}/api/v1/openapi.json`（OpenAPI 3）、在线调试页 `{{BASE_URL}}/api/v1/docs`。
> 文档本身无需鉴权，调用接口需要 API Key。

claude-register 是一个自动注册 claude.ai 账号的服务。开放 API 让外部程序：

1. **触发一次自动注册**（后台运行，通常需要 1–5 分钟）；
2. **查询注册结果**，成功后按需选择返回哪些账号信息；
3. **批量导出账号**，可选字段、格式和筛选条件；
4. **逐个获取账号**：每调用一次拿到一个可用账号，并自动标记为「已获取」，不会重复发放。

- Base URL：`{{BASE_URL}}/api/v1`
- 编码：请求与响应均为 UTF-8；JSON 请求需带 `Content-Type: application/json`
- 时间：ISO 8601 UTC，如 `2026-09-24T08:00:00Z`

## 鉴权

所有 `/api/v1/*` 业务接口都需要 API Key，二选一放在请求头里：

```
Authorization: Bearer <API_KEY>
X-API-Key: <API_KEY>
```

- API Key 由服务管理员在面板「系统设置 → 开放 API」启用并生成；重新生成后旧 Key 立即失效。
- **不接受**把 Key 放在 URL 参数里。
- 未启用开放 API 返回 `403`；缺少或错误的 Key 返回 `401`。

## 快速开始

```bash
BASE={{BASE_URL}}/api/v1
KEY=cr_xxxxxxxx            # 向管理员索取

# 1) 触发注册
RID=$(curl -s -X POST -H "Authorization: Bearer $KEY" "$BASE/register" | jq .run_id)

# 2) 轮询到完成（每次最多等待 {{MAX_WAIT}} 秒），只取邮箱、sessionKey、密码
while :; do
  R=$(curl -s -H "Authorization: Bearer $KEY" \
    "$BASE/register/$RID?wait={{MAX_WAIT}}&fields=email,session_key,password")
  [ "$(echo "$R" | jq -r .status)" != running ] && break
done
echo "$R" | jq .
```

Python：

```python
import requests

BASE = "{{BASE_URL}}/api/v1"
H = {"Authorization": "Bearer cr_xxxxxxxx"}

run_id = requests.post(f"{BASE}/register", headers=H, json={}).json()["run_id"]
while True:
    r = requests.get(f"{BASE}/register/{run_id}", headers=H, timeout={{MAX_WAIT}} + 10,
                     params={"wait": {{MAX_WAIT}}, "fields": "email,session_key,password"}).json()
    if r["status"] != "running":
        break
if r["status"] == "success":
    print(r["account"])   # {"email": ..., "session_key": ..., "password": ...}
else:
    print(r["status"], r.get("log_tail"))
```

## 接口一览

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/v1/register` | 触发一次自动注册 |
| `GET` | `/api/v1/register/{run_id}` | 查询注册结果（支持长轮询、字段选择） |
| `GET` | `/api/v1/accounts/export` | 批量导出账号 |
| `POST` | `/api/v1/accounts/claim` | 获取一个账号并标记为已获取 |
| `GET` | `/api/v1/fields` | 可导出的字段与格式 |
| `GET` | `/api/v1/proxies` | 可选的注册代理 |

### POST /api/v1/register

触发一次自动注册。**同一时刻只能运行一个注册任务**。

请求体（JSON，可省略，所有字段可选）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `email` | string | 指定要注册的邮箱；不填则自动创建临时邮箱 |
| `domain` | string | 自动创建邮箱时使用的域名；不填用服务默认域名 |
| `proxy_id` | string | 使用代理池中的某个代理（id 见 `GET /api/v1/proxies`）；不填用服务默认代理 |

响应：

- `202 Accepted`：`{"run_id": 12, "status": "running"}`
- `409 Conflict`：已有任务在运行，`{"detail": "已有任务在运行", "active_run_id": 11}`。
  可以等待 `active_run_id` 结束后再提交。
- `400 Bad Request`：请求体不是 JSON 对象、字段类型错误、`proxy_id` 不存在或无效。

### GET /api/v1/register/{run_id}

查询注册任务的状态与结果。

| Query 参数 | 默认 | 说明 |
|---|---|---|
| `wait` | `0` | 长轮询：任务仍在运行时最多等待的秒数，`0`–`{{MAX_WAIT}}`；任务结束会立即返回 |
| `fields` | 默认五项 | 逗号分隔的字段名，决定 `account` 里返回哪些信息，见「字段」 |
| `format` | `json` | 设为 `text` / `csv` / `line` 时，额外返回 `export` 文本 |
| `sep` | `----` | `format=line` 时的分隔符（1–16 个字符，不能含换行） |

响应 `200`：

```json
{
  "run_id": 12,
  "status": "success",
  "email": "alice@example.com",
  "started_at": "2026-09-24T08:00:00Z",
  "finished_at": "2026-09-24T08:02:31Z",
  "account": {"email": "alice@example.com", "session_key": "sk-ant-sid01-...", "password": "..."},
  "export": "alice@example.com----sk-ant-sid01-...----..."
}
```

`status` 取值：

| 值 | 含义 | 附带内容 |
|---|---|---|
| `running` | 仍在进行 | `account` 为 `null` |
| `success` | 注册成功并拿到 sessionKey | `account`（及 `export`） |
| `needs_manual` | 流程跑完但没拿到 sessionKey；账号已入库，需管理员在面板「接管」处理 | `account`、`log_tail` |
| `failed` | 注册失败 | `account` 为 `null`，`log_tail` 为最近 20 行日志 |

- `export` 仅在 `format` 不是 `json` 且有账号时出现。
- `404`：`run_id` 不存在；`400`：`fields` / `format` / `sep` 不合法；`422`：`wait` 超出范围。

### GET /api/v1/accounts/export

批量导出已入库的账号（按创建时间倒序）。

| Query 参数 | 默认 | 说明 |
|---|---|---|
| `fields` | 默认五项 | 逗号分隔的字段名，输出按给出的顺序 |
| `format` | `json` | `json` / `text` / `csv` / `line`，见「导出格式」 |
| `sep` | `----` | `format=line` 的分隔符 |
| `status` | 不筛选 | 注册状态：`success` / `needs_manual` |
| `check_status` | 不筛选 | 最近一次存活检测结果：`alive` / `dead` / `blocked` / `error` |
| `emails` | 不筛选 | 逗号分隔的邮箱列表（不区分大小写） |
| `claimed` | 不筛选 | `true` 只导出已获取的账号，`false` 只导出未获取的 |

- 批量导出**不会**打「已获取」标记；需要逐个发放账号时请用 `POST /api/v1/accounts/claim`。
- 响应体为对应格式的内容；响应头 `X-Total-Count` 为导出条数。
- 没有符合条件的账号时返回空内容（`json` 为 `[]`），状态码仍为 `200`。

示例：

```bash
# 所有检测有效的账号，导出为 CSV
curl -H "Authorization: Bearer $KEY" \
  "$BASE/accounts/export?format=csv&check_status=alive&fields=email,session_key,proxy" -o alive.csv

# 指定邮箱，单行格式：email----sessionKey
curl -H "Authorization: Bearer $KEY" \
  "$BASE/accounts/export?format=line&fields=email,session_key&emails=a@x.com,b@x.com"
```

### POST /api/v1/accounts/claim

获取一个账号：每调用一次返回**一个**可用账号，并立即打上「已获取」标记（记录 `claimed_at`）。
已获取的账号不会再被发放；并发调用也不会拿到同一个账号。

可获取的账号：注册状态为 `success`、有 sessionKey、尚未被获取；默认跳过最近检测为 `dead` /
`blocked` 的账号。按入库顺序先进先出。

| Query 参数 | 默认 | 说明 |
|---|---|---|
| `fields` | 默认五项 | 逗号分隔的字段名，决定 `account` 里返回哪些信息 |
| `format` | `json` | 设为 `text` / `csv` / `line` 时，额外返回 `export` 文本 |
| `sep` | `----` | `format=line` 时的分隔符 |
| `check_status` | 不筛选 | 只获取该检测结果的账号，如 `alive`（只发最近检测有效的） |

响应 `200`：

```json
{
  "email": "alice@example.com",
  "claimed_at": "2026-09-24T08:10:00Z",
  "account": {"email": "alice@example.com", "session_key": "sk-ant-sid01-..."},
  "export": "alice@example.com----sk-ant-sid01-...",
  "remaining": 7
}
```

- `remaining`：同样条件下还剩多少个可获取的账号。
- `export` 仅在 `format` 不是 `json` 时出现。
- `404`：没有可获取的账号（`{"detail": "没有可获取的账号"}`），可先触发注册再重试。
- `400`：`fields` / `format` / `sep` / `check_status` 不合法。
- 管理员可在面板账号列表里看到「已获取」标签，并手动取消标记让账号重新可被获取。

示例：

```bash
curl -X POST -H "Authorization: Bearer $KEY" \
  "$BASE/accounts/claim?format=line&fields=email,session_key" | jq -r .export
```

### GET /api/v1/fields

返回可导出的字段、默认字段与支持的格式：

```json
{
  "fields": [{"key": "email", "title": "邮箱", "label": "email", "default": true, "secret": false}],
  "formats": ["text", "json", "csv", "line"],
  "default_fields": ["email", "session_key", "proxy", "mail_base_url", "mail_key"],
  "default_line_sep": "----"
}
```

### GET /api/v1/proxies

返回代理池中可用于 `proxy_id` 的代理，只含 id 和名称（不暴露地址与凭据）：

```json
[{"id": "us-1", "name": "美国 1"}]
```

## 字段

`fields` 参数可用的字段（默认：`email,session_key,proxy,mail_base_url,mail_key`）：

{{FIELDS_TABLE}}

标记为「敏感」的字段是登录凭据，请妥善保存，不要写入公开日志。

## 导出格式

| `format` | 说明 | 示例（字段 `email,session_key`） |
|---|---|---|
| `json` | JSON 数组，每个账号一个对象 | `[{"email": "a@x.com", "session_key": "sk-..."}]` |
| `text` | 每个账号一段「标签：值」，段间空行；标签见字段表 | `email：a@x.com` 换行 `sessionkey：sk-...` |
| `csv` | 首行为字段名，UTF-8 带 BOM（Excel 可直接打开） | `email,session_key` 换行 `a@x.com,sk-...` |
| `line` | 每个账号一行，字段值用 `sep` 连接 | `a@x.com----sk-...` |

## 错误

错误响应统一为 JSON：`{"detail": "说明"}`。

| 状态码 | 场景 |
|---|---|
| `400` | 参数或请求体不合法（含未知字段、未知格式、代理不存在） |
| `401` | 缺少或错误的 API Key |
| `403` | 服务未启用开放 API |
| `404` | 注册任务不存在；或没有可获取的账号（`accounts/claim`） |
| `409` | 已有注册任务在运行（响应带 `active_run_id`） |
| `422` | Query 参数类型或范围错误（如 `wait` 超过 {{MAX_WAIT}}） |

## 使用建议

- 注册是串行的：批量注册时，等上一个 `run_id` 结束（状态不再是 `running`）再提交下一个；
  收到 `409` 时等待 `active_run_id` 结束即可。
- 长轮询 `wait` 最大 {{MAX_WAIT}} 秒，客户端超时请设得比它长；任务未完成就继续调用。
- `needs_manual` 和 `failed` 不代表服务故障，可根据 `log_tail` 判断原因后重试。
- sessionKey 是会话级凭据，可能随时间失效；导出时可用 `check_status=alive` 只取最近检测有效的账号。
