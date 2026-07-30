"""跨多台 3x-ui 节点的按账号开号器。

provision：随机挑一台节点 → 在该节点选空闲端口 → 建 mixed(socks5) inbound
（inbound 级 expiryTime = 现在 + expiry_days）→ 返回 socks5 URL + 撤销句柄。
节点不可达/登录失败自动切下一台。revoke/cleanup_expired 供失败回收与面板清理。
"""

from __future__ import annotations

import string
from collections.abc import Callable
from dataclasses import dataclass

from claude_register.console import log
from claude_register.xui import XuiClient, XuiError

_DAY_MS = 86_400 * 1_000
_PORT_TRIES = 8  # 同一节点内换端口重试次数（撞端口用）
_CRED_ALPHABET = string.ascii_letters + string.digits


@dataclass(frozen=True)
class XuiNode:
    name: str
    base_url: str
    username: str
    password: str
    proxy_host: str = ""


@dataclass(frozen=True)
class ProvisionedProxy:
    url: str
    node_name: str
    inbound_id: int
    expiry_ms: int


class ProxyPoolError(Exception):
    """全部节点都开号失败时抛出。"""


def _rand_cred(rng, n: int = 12) -> str:
    return "".join(rng.choice(_CRED_ALPHABET) for _ in range(n))


class ProxyPool:
    def __init__(
        self,
        nodes,
        *,
        expiry_days: int,
        port_range,
        client_factory=XuiClient,
        now_ms: Callable[[], int] | None = None,
        rng=None,
    ) -> None:
        self.nodes = list(nodes)
        self.expiry_days = int(expiry_days)
        self.port_lo, self.port_hi = int(port_range[0]), int(port_range[1])
        self._factory = client_factory
        self._now_ms = now_ms or _default_now_ms
        import random as _random

        self._rng = rng or _random.Random()

    def _client(self, node: XuiNode) -> XuiClient:
        return self._factory(
            node.base_url, node.username, node.password, proxy_host=node.proxy_host
        )

    def provision(self, email: str) -> ProvisionedProxy:
        if not self.nodes:
            raise ProxyPoolError("未配置任何 3x-ui 节点")
        expiry_ms = self._now_ms() + self.expiry_days * _DAY_MS
        remark = f"reg:{email}"
        order = list(self.nodes)
        self._rng.shuffle(order)
        last_err: Exception | None = None
        for node in order:
            try:
                client = self._client(node)
                used = {ib.get("port") for ib in client.list_inbounds()}
                proxy = self._provision_on(client, node, used, expiry_ms, remark)
                if proxy is not None:
                    return proxy
            except XuiError as exc:  # 登录失败/列表失败 → 换下一台
                last_err = exc
                log(f"节点 {node.name} 开号失败（{exc}），尝试下一台。")
        raise ProxyPoolError(f"所有节点均开号失败：{last_err}")

    def _provision_on(self, client, node, used, expiry_ms, remark):
        for _ in range(_PORT_TRIES):
            port = self._rng.randint(self.port_lo, self.port_hi)
            if port in used:
                continue
            user, password = _rand_cred(self._rng), _rand_cred(self._rng)
            try:
                iid = client.create_socks_inbound(
                    user, password, port, expiry_ms, remark
                )
            except XuiError as exc:
                used.add(port)  # 撞端口或该端口不可用，换一个再试
                log(f"节点 {node.name} 端口 {port} 建号失败（{exc}），换端口。")
                continue
            url = f"socks5://{user}:{password}@{client.proxy_host}:{port}"
            return ProvisionedProxy(
                url=url, node_name=node.name, inbound_id=iid, expiry_ms=expiry_ms
            )
        return None  # 端口重试用尽，让 provision 换下一台节点

    def revoke(self, proxy: ProvisionedProxy) -> None:
        node = next((n for n in self.nodes if n.name == proxy.node_name), None)
        if node is None:
            log(f"撤销代理找不到节点 {proxy.node_name}，忽略。")
            return
        try:
            self._client(node).delete_inbound(proxy.inbound_id)
        except XuiError as exc:
            log(f"撤销代理 inbound {proxy.inbound_id} 失败（{exc}），忽略。")

    def cleanup_expired(self) -> dict[str, int]:
        now = self._now_ms()
        result: dict[str, int] = {}
        for node in self.nodes:
            deleted = 0
            try:
                client = self._client(node)
                for ib in client.list_inbounds():
                    remark = str(ib.get("remark") or "")
                    exp = int(ib.get("expiryTime") or 0)
                    if remark.startswith("reg:") and 0 < exp < now:
                        client.delete_inbound(int(ib["id"]))
                        deleted += 1
            except XuiError as exc:
                log(f"清理节点 {node.name} 过期 inbound 失败（{exc}），跳过。")
            result[node.name] = deleted
        return result


def _default_now_ms() -> int:
    import time

    return int(time.time() * 1000)
