from __future__ import annotations

import random

import pytest

from claude_register.proxy_pool import (
    ProvisionedProxy,
    ProxyPool,
    ProxyPoolError,
    XuiNode,
)
from claude_register.xui import XuiError


class FakeXui:
    """记录调用的假客户端；行为按 name 从 registry 取。"""

    registry: dict = {}

    def __init__(self, base_url, username, password, *, proxy_host="", timeout=30.0):
        self.base_url = base_url
        self.proxy_host = proxy_host or "host-" + username
        self.spec = FakeXui.registry[username]  # 用 username 当 key 区分节点
        self.created: list = []

    def list_inbounds(self):
        if self.spec.get("down"):
            raise XuiError("node down")
        return self.spec.get("inbounds", [])

    def create_socks_inbound(self, user, password, port, expiry_ms, remark):
        self.spec.setdefault("attempts", []).append(port)
        if port in self.spec.get("taken", set()):
            raise XuiError("port already in use")
        self.created.append((user, password, port, expiry_ms, remark))
        self.spec.setdefault("created_ids", []).append(port)
        self.spec["last_remark"] = remark
        return port  # 用 port 当 inbound id 方便断言

    def delete_inbound(self, inbound_id):
        self.spec.setdefault("deleted", []).append(inbound_id)


class ScriptedRandom:
    """确定性 rng：shuffle 保持原顺序，randint 依次弹出脚本端口，choice 取第一个。"""
    def __init__(self, ports):
        self._ports = list(ports)
    def shuffle(self, seq):
        pass  # 恒等：保留调用方给的节点顺序，便于断言 failover
    def randint(self, lo, hi):
        return self._ports.pop(0)
    def choice(self, seq):
        return seq[0]


def _node(name, username):
    return XuiNode(name=name, base_url=f"https://{name}.test:2053", username=username,
                   password="pw", proxy_host=f"{name}.example.com")


def _pool(nodes, specs, *, seed=0, now_ms=1_000, rng=None):
    FakeXui.registry = specs
    return ProxyPool(
        nodes,
        expiry_days=30,
        port_range=(45000, 45010),
        client_factory=FakeXui,
        now_ms=lambda: now_ms,
        rng=rng if rng is not None else random.Random(seed),
    )


def test_provision_builds_socks_url_with_expiry_and_remark():
    specs = {"u1": {"inbounds": []}}
    pool = _pool([_node("n1", "u1")], specs, now_ms=1_000)
    got = pool.provision("x@mail.test")
    assert isinstance(got, ProvisionedProxy)
    assert got.node_name == "n1"
    assert got.url.startswith("socks5://")
    assert "@n1.example.com:" in got.url
    # 30 天 = 30*86400*1000 毫秒后到期
    assert got.expiry_ms == 1_000 + 30 * 86_400 * 1_000
    assert remark_of(specs, "u1") == "reg:x@mail.test"


def remark_of(specs, u):
    # 便捷取回最后一次 create 的 remark
    return specs[u]["last_remark"]


def test_provision_avoids_used_ports():
    specs = {"u1": {"inbounds": [{"port": 45000}, {"port": 45001}], "last_remark": ""}}
    # 脚本先给 45000（已被占用，必须跳过），再给 45002（可用）
    pool = _pool([_node("n1", "u1")], specs, rng=ScriptedRandom([45000, 45002]))
    got = pool.provision("x@mail.test")
    assert got.inbound_id == 45002
    # 45000 命中 used，绝不能尝试建号
    assert specs["u1"].get("attempts", []) == [45002]


def test_provision_retries_on_port_taken_then_succeeds():
    specs = {"u1": {"inbounds": [], "taken": {45000}, "last_remark": ""}}
    # 45000 建号抛 XuiError，重试 45003 成功
    pool = _pool([_node("n1", "u1")], specs, rng=ScriptedRandom([45000, 45003]))
    got = pool.provision("x@mail.test")
    assert got.inbound_id == 45003
    # 确实先试了 45000（撞端口）才换到 45003
    assert specs["u1"]["attempts"] == [45000, 45003]


def test_provision_falls_over_to_next_node_when_first_down():
    specs = {"bad": {"down": True}, "good": {"inbounds": [], "last_remark": ""}}
    # shuffle 恒等 → "bad" 仍排第一；bad 不可达 → failover 到 good
    pool = _pool([_node("n1", "bad"), _node("n2", "good")], specs,
                 rng=ScriptedRandom([45005]))
    got = pool.provision("x@mail.test")
    assert got.node_name == "n2"


def test_provision_raises_when_all_nodes_fail():
    specs = {"bad": {"down": True}}
    pool = _pool([_node("n1", "bad")], specs)
    with pytest.raises(ProxyPoolError):
        pool.provision("x@mail.test")


def test_revoke_deletes_on_matching_node():
    specs = {"u1": {"inbounds": [], "last_remark": ""}}
    pool = _pool([_node("n1", "u1")], specs)
    pool.revoke(ProvisionedProxy(url="socks5://a:b@h:1", node_name="n1",
                                 inbound_id=99, expiry_ms=0))
    assert specs["u1"]["deleted"] == [99]


def test_cleanup_expired_deletes_only_expired_reg_inbounds():
    specs = {"u1": {"inbounds": [
        {"id": 1, "remark": "reg:a@m", "expiryTime": 500},    # 已过期
        {"id": 2, "remark": "reg:b@m", "expiryTime": 5000},   # 未过期
        {"id": 3, "remark": "reg:c@m", "expiryTime": 0},      # 永久，跳过
        {"id": 4, "remark": "manual", "expiryTime": 100},     # 非 reg:，跳过
    ], "last_remark": ""}}
    pool = _pool([_node("n1", "u1")], specs, now_ms=1_000)
    res = pool.cleanup_expired()
    assert res == {"n1": 1}
    assert specs["u1"]["deleted"] == [1]
