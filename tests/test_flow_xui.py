"""flow 接入 3x-ui 代理池：开号→失败撤销→成功绑定导出；未启用时退回静态代理。"""

from __future__ import annotations

import pytest

from claude_register import anymail, flow
from claude_register.proxy_pool import ProvisionedProxy
from server.config_store import Config


def _mailbox():
    return anymail.Mailbox(id="m1", email="x@mail.test",
                           expires_at="2026-07-31T00:00:00Z")


def _cfg(**kw):
    base = dict(
        anymail_api_key="ak_parent", anymail_base_url="https://mail.test",
        anymail_domain="mail.test",
        xui_enabled=True, xui_expiry_days=30,
        xui_port_min=45000, xui_port_max=45010,
        xui_nodes=({"name": "n1", "base_url": "https://n1.test:2053",
                    "username": "u", "password": "p", "proxy_host": "n1.example.com"},),
    )
    base.update(kw)
    return Config(**base)


@pytest.fixture
def wired(monkeypatch):
    seen = {"revoked": [], "proxies": [], "saved": []}

    monkeypatch.setattr(flow, "validate_proxy", lambda proxy: None)
    monkeypatch.setattr(
        flow, "prepare_mailbox",
        lambda client, **kw: (_mailbox(), "2026-07-30T00:00:00Z"),
    )
    # 子 key 派生关掉，聚焦代理路径
    monkeypatch.setattr(anymail.AnyMailClient, "create_child_key",
                        lambda self, **kw: None)

    class FakePool:
        def provision(self, email):
            p = ProvisionedProxy(url="socks5://a:b@n1.example.com:45001",
                                 node_name="n1", inbound_id=45001,
                                 expiry_ms=99)
            seen["proxies"].append(p)
            return p

        def revoke(self, proxy):
            seen["revoked"].append(proxy.inbound_id)

    monkeypatch.setattr(flow, "_build_proxy_pool", lambda config: FakePool())

    def fake_run_browser(client, mailbox, since, *, proxy=None, **kw):
        seen["proxy_used"] = proxy
        return seen.get("browser_result")

    monkeypatch.setattr(flow, "run_browser", fake_run_browser)
    return seen


def test_provisions_proxy_and_passes_to_browser(wired):
    wired["browser_result"] = {"sessionKey": "sk-1"}
    flow.run(config=_cfg())
    assert wired["proxy_used"] == "socks5://a:b@n1.example.com:45001"
    assert wired["revoked"] == []  # 成功不撤销


def test_revokes_proxy_when_no_session_key(wired):
    wired["browser_result"] = None  # 未拿到 sessionKey
    flow.run(config=_cfg())
    assert wired["revoked"] == [45001]


def test_revokes_proxy_when_browser_raises(wired, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("browser crash")
    monkeypatch.setattr(flow, "run_browser", boom)
    with pytest.raises(RuntimeError):
        flow.run(config=_cfg())
    assert wired["revoked"] == [45001]


def test_disabled_falls_back_to_static_proxy(wired, monkeypatch):
    monkeypatch.setattr(flow, "_build_proxy_pool", lambda config: None)
    wired["browser_result"] = {"sessionKey": "sk-1"}
    flow.run(config=_cfg(xui_enabled=False, register_proxy="socks5://static:1080"))
    assert wired["proxy_used"] == "socks5://static:1080"
    assert wired["revoked"] == []
