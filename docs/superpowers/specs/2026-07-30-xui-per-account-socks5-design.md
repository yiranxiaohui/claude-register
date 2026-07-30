# 注册时按账号从 3x-ui 池动态开专属 SOCKS5

日期：2026-07-30
状态：已确认，待实现

## 背景与目标

现有注册流程使用**一个静态配置的** `register.proxy`（socks5，带认证时经本地免认证中继喂给浏览器）。本次要把它升级为：**注册每个 Claude 账号时，在 3x-ui 节点池里动态开一个本账号专属的 SOCKS5 代理**，绑定到账号并随账号导出，供该账号后续持续使用；注册失败即撤销。

代理复用现有「派生本邮箱专用 AnyMail 子 key → 失败撤销 → 成功导出」的模式：代理从「静态一个」变成「每账号动态一个」。

## 已在真实面板（usa-4）验证的技术事实

- 登录：`POST {base_url}/login`，表单 `username`/`password` → 返回 `{"success":true,...}`，下发 `3x-ui` cookie（HttpOnly）。后续 API 带此 cookie。无独立 API token。
- **SOCKS5 出口 = `mixed` 协议 inbound**（socks5+http 合体），设置为 `{"auth":"password","accounts":[{"user":...,"pass":...}],"udp":true,"ip":"127.0.0.1"}`。
- **socks/mixed 的 account 无「按账号到期」字段**；有效期只能挂在 **inbound 级 `expiryTime`**（Unix 毫秒），到期 3x-ui 自动禁用（但**不自动删除**，会堆积）。
- 建 inbound：`POST {base}/panel/api/inbounds/add`，字段 `remark/enable/expiryTime/listen/port/protocol/settings/streamSettings/sniffing/allocate`（`settings` 等为 JSON 字符串）。返回 `obj.id`。
- 删 inbound：`POST {base}/panel/api/inbounds/del/{id}`。
- 列 inbound：`GET {base}/panel/api/inbounds/list` → `obj[]`（含 `id/port/protocol/remark/enable/expiryTime/settings`）。
- 代理地址 = `socks5://user:pass@<proxy_host>:<port>`，`proxy_host` 默认取 `base_url` 主机名；出口是该节点自身公网 IP，**同节点所有账号共用同一出口 IP**。
- 面板可能带自定义 base path（如 `.../5XOrf2HJAUEP0gfcPT`），所有路径都基于 `base_url`。

## 架构

### 新模块 `claude_register/xui.py` — 单台 3x-ui API 客户端
- `XuiClient(base_url, username, password, proxy_host="", timeout=...)`
- `login()`：POST /login，缓存 cookie；`_request()` 遇 401/未登录自动重登一次。
- `list_inbounds() -> list[dict]`
- `create_socks_inbound(user, password, port, expiry_ms, remark) -> int`：建 `mixed` inbound，返回新 inbound id。
- `delete_inbound(inbound_id)`。
- 自签证书：请求关闭证书校验（等价 `curl -k`）。

### 新模块 `claude_register/proxy_pool.py` — 跨节点开号器
- 输入：节点列表 + `expiry_days` + `port_range`。
- `provision(email) -> ProvisionedProxy`：
  1. **随机挑**一台节点（无状态；CLI/面板行为一致；分布够均匀）。节点不可达/登录失败 → 换下一台；全部失败 → 抛错。
  2. 在该节点选空闲端口：随机取 `port_range` 内值，`list_inbounds()` 避开已占端口；`add` 撞端口报错则重试若干次。
  3. 生成随机 `user`/`pass`（沿用现有 10 字符风格）。
  4. `create_socks_inbound(..., expiry_ms=now+expiry_days, remark=f"reg:{email}")`。
  5. 返回 `ProvisionedProxy(url="socks5://user:pass@proxy_host:port", node_name, inbound_id, expiry_ms)`。
- `revoke(proxy: ProvisionedProxy)`：调对应节点 `delete_inbound`。
- `cleanup_expired(node) -> int`：删除该节点上 `remark` 以 `reg:` 开头且 `expiryTime>0 且 < now` 的 inbound，返回删除数（供面板手动清理按钮）。

### 配置（config.yaml 新增 `xui` 段）
```yaml
xui:
  enabled: true
  expiry_days: 30
  port_range: [40000, 60000]
  nodes:
    - name: usa-4
      base_url: https://usa-4.xyprohani.xyz:2053/5XOrf2HJAUEP0gfcPT
      username: ZZrXnIlWDI
      password: w1RuyjoYIn
      proxy_host: usa-4.xyprohani.xyz   # 留空则取 base_url 主机名
```
`server/config_store.py` 扩展：现有 `_FIELD_MAP` 是扁平 dataclass，`nodes` 是嵌套列表，需单独处理该段的读写。GET 时每个 node 的 `password` 脱敏为 `••••`，保存时留空/脱敏值 = 不改（与现有 `panel_password`/`anymail_api_key` 脱敏逻辑一致）。

### 注册流程接入（`claude_register/flow.py`）
**开号点定死**：放在 `prepare_mailbox`（拿到真实邮箱名）之后、`run_browser` 之前，这样 remark 能写真实邮箱便于溯源。`validate_proxy` 只在 `xui.enabled=false`（静态代理）时才有意义；启用节点池时该静态校验跳过。

- `xui.enabled` 为真：`prepare_mailbox` 拿到 `mailbox.email` 后，调 `proxy_pool.provision(mailbox.email)` 开专属代理（remark=`reg:{email}`），用返回 URL 覆盖 `proxy` 变量，照旧传入 `run_browser`（走本地免认证中继）。
  - **成功**（`account.sessionKey` 存在）：`record.proxy = url`，`record.extra["xui"] = {node, inbound_id, expiry_ms}`，随账号导出。
  - **失败/中断**（异常或未拿到 sessionKey）：`proxy_pool.revoke(...)` 删 inbound。与现有 `client.delete_key(child.id)` 两处回收点对称放置（同一个 `try/except` + 事后判 sessionKey 的结构里加一路代理回收）。
  - `provision` 自身失败（全节点不可达）：在 `prepare_mailbox` 之后抛出，此时已建的邮箱与子 key 沿用现有中断回收路径撤销。
- `xui.enabled` 为假：跑现有 `validate_proxy(register_proxy)` 并退回静态 `register_proxy`，完全向后兼容。

### 面板（Settings 页 + 后端）
- Settings 新增「代理池」区块：`enabled` 开关、`expiry_days`、`port_range`，以及**节点表格**（增/删/改：`name`、`base_url`、`username`、`password`、`proxy_host`）。
- 后端节点 CRUD：并入 `PUT /api/config` 或新增专用接口；GET 时 node 密码脱敏。
- 「测试连接」按钮：对一台节点调 `login` + `list_inbounds` 验证凭据可用。
- 「清理过期 inbound」按钮：对所有节点调 `cleanup_expired`，返回各节点删除数（手动触发，非定时任务）。

## 错误处理

- 节点登录失败/不可达 → 换下一台；全部失败 → 明确报错，不建邮箱。
- 端口范围取尽/多次撞端口 → 报错。
- inbound 到期由 3x-ui 自动禁用；不做自动回收任务，堆积由手动清理按钮处理。
- 导出记录带 `expiry`，便于账号使用方知晓代理有效期。

## 测试

- `xui.py`：mock HTTP，覆盖 login、create/delete inbound、cookie 失效自动重登、自签证书跳过校验。
- `proxy_pool.py`：节点随机挑选与失败切换、端口避让 + 撞端口重试、`provision` 返回结构、`revoke`、`cleanup_expired` 过滤条件。
- `config_store`：带 `nodes` 列表的读写往返 + node 密码脱敏 + 留空不改。
- `flow`：注册失败路径确实调用 `revoke`（复用现有失败回收测试骨架）。
- 手动/可选：对真实 usa-4 节点跑一次端到端建号→出网→删号（本设计阶段已手工验证通过）。

## 非目标（YAGNI）

- 不做自动到期回收定时任务（改为手动清理按钮）。
- 不做按账号独立出口 IP（受限于每节点单出口 IP；多 IP 靠多节点）。
- 不做代理流量统计/限速管理。
- 不改动现有本地免认证 SOCKS5 中继逻辑。
