"""单台 3x-ui 面板的 API 客户端。

封装已验证过的调用：POST /login 拿 3x-ui cookie；GET /panel/api/inbounds/list；
POST /panel/api/inbounds/add（mixed 协议 = socks5+http，用 accounts 认证）；
POST /panel/api/inbounds/del/{id}。3x-ui 自签证书，请求一律 verify=False。
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit

import httpx

from claude_register.console import log

# mixed inbound 的固定 sniffing/allocate；socks 场景用不到，给合法空值即可。
_SNIFFING = json.dumps({"enabled": False, "destOverride": []})


class XuiError(Exception):
    """3x-ui 返回 success=false 或响应异形时抛出。"""


class XuiClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        proxy_host: str = "",
        timeout: float = 30.0,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.username = username
        self.password = password
        self.proxy_host = (proxy_host or "").strip() or (urlsplit(self.base_url).hostname or "")
        self.timeout = timeout
        self._cookies: httpx.Cookies | None = None

    def _new_client(self) -> httpx.Client:
        return httpx.Client(timeout=self.timeout, verify=False)

    def login(self) -> None:
        with self._new_client() as c:
            resp = c.post(
                f"{self.base_url}/login",
                data={"username": self.username, "password": self.password},
            )
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError as exc:
            raise XuiError(f"登录响应非 JSON：{resp.text[:120]}") from exc
        if not data.get("success"):
            raise XuiError(f"3x-ui 登录失败：{data.get('msg')!r}")
        self._cookies = resp.cookies

    def _call(self, method: str, path: str, *, data: dict | None = None) -> dict:
        """带 cookie 调 API；会话失效（返回非 JSON/登录页）时自动重登一次。"""
        if self._cookies is None:
            self.login()
        parsed = self._request_json(method, path, data)
        if parsed is None:  # 会话失效
            self.login()
            parsed = self._request_json(method, path, data)
        if parsed is None:
            raise XuiError(f"{method} {path} 会话重登后仍未拿到 JSON 响应")
        if not parsed.get("success"):
            raise XuiError(f"{method} {path} 失败：{parsed.get('msg')!r}")
        return parsed

    def _request_json(self, method: str, path: str, data: dict | None) -> dict | None:
        with self._new_client() as c:
            resp = c.request(
                method, f"{self.base_url}{path}", cookies=self._cookies, data=data
            )
        if resp.is_redirect:
            return None
        try:
            return resp.json()
        except ValueError:
            return None  # 多半是被打回登录页 HTML

    def list_inbounds(self) -> list[dict]:
        data = self._call("GET", "/panel/api/inbounds/list")
        obj = data.get("obj")
        return list(obj) if isinstance(obj, list) else []

    def create_socks_inbound(
        self, user: str, password: str, port: int, expiry_ms: int, remark: str
    ) -> int:
        settings = json.dumps(
            {
                "auth": "password",
                "accounts": [{"user": user, "pass": password}],
                "udp": True,
                "ip": "127.0.0.1",
            }
        )
        form = {
            "remark": remark,
            "enable": "true",
            "expiryTime": str(expiry_ms),
            "listen": "",
            "port": str(port),
            "protocol": "mixed",
            "settings": settings,
            "streamSettings": "{}",
            "sniffing": _SNIFFING,
            "allocate": "{}",
        }
        data = self._call("POST", "/panel/api/inbounds/add", data=form)
        obj = data.get("obj") or {}
        iid = obj.get("id") if isinstance(obj, dict) else None
        if iid is None:
            raise XuiError(f"建 inbound 响应缺 id：{data!r}")
        return int(iid)

    def delete_inbound(self, inbound_id: int) -> None:
        try:
            self._call("POST", f"/panel/api/inbounds/del/{inbound_id}")
        except Exception as exc:  # noqa: BLE001
            # delete 是回收路径，任何失败都只警告，inbound 带 expiryTime 兜底。
            log(f"删除 inbound {inbound_id} 失败（{exc}），忽略。")
