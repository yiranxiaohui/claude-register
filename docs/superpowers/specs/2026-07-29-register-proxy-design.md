# 注册代理支持（单个固定代理）设计

日期：2026-07-29

## 背景与目标

注册 claude.ai 账号时浏览器需要经代理出口，以规避同 IP 多号风控。本期只做**单个固定代理**：所有注册会话走同一个代理；留空则直连。代理池/轮换明确不做（YAGNI，将来有需要再加）。

只有浏览器（Camoufox 访问 claude.ai）走代理；anymail 接码 API 保持直连。

## 配置

- `config.yaml` 新增 `register.proxy`，字符串，默认 `""`（直连）。
- 接受标准代理 URL：
  - `http://host:port`
  - `http://user:pass@host:port`
  - `socks5://host:port`（含认证同理）
- 设置页（`web/src/pages/Settings.jsx`）注册区块新增「注册代理」文本框，placeholder 提示格式，说明留空直连。
- 明文回显，不脱敏：面板本身有密码保护；脱敏会连 host 都看不到，弊大于利。

## 浏览器接入

- `claude_register/browser.py`：
  - 新增解析函数 `parse_proxy(url: str) -> dict | None`：
    - 空/空白 → `None`（直连）。
    - 合法 URL → Playwright 风格 `{"server": "scheme://host:port", "username": ..., "password": ...}`（无认证时不含 username/password 键）。
    - 非法（无 scheme、无 host、无法解析）→ 抛 `ValueError`，带可读的中文错误信息。**不静默降级直连**。
  - `browser_session()` 增加 `proxy: str | None = None` 参数，解析后传给 `Camoufox(proxy=...)`。
  - `geoip=True` 保持不变：Camoufox 按代理出口 IP 匹配时区/地理指纹，正是期望行为。
  - 启动日志打印代理 server（不打印认证信息）。

## 贯通

- `server/config_store.py`：`Config` 增加 `register_proxy: str = ""`，`_FIELD_MAP` 增加 `("register", "proxy")` 映射。
- `claude_register/flow.py`：注册流程把配置中的代理传给 `browser_session(proxy=...)`。
- `server/runner.py` → flow 的参数链路按现有模式补一个字段。
- anymail 客户端不改。

## 错误处理

- 代理 URL 非法：注册任务启动阶段即失败，日志给出格式示例。
- 代理不可达/超时：表现为页面加载失败/登录表单超时，沿用现有的截图+报错路径，不做额外重试。

## 测试

- `parse_proxy` 单元测试：http/socks5、带认证/不带认证、空串、非法串（无 scheme、纯 host、乱码）。
- `browser_session` 的传参不加集成测试（CI 无真实代理可用）。
