# tests/test_xui.py
from __future__ import annotations

import json

import httpx
import pytest
import respx

from claude_register.xui import XuiClient, XuiError

BASE = "https://panel.test:2053/secretpath"


def _login_route(router, *, ok=True):
    return router.post(f"{BASE}/login").mock(
        return_value=httpx.Response(
            200,
            json={"success": ok, "msg": "" if ok else "wrong", "obj": None},
            headers={"set-cookie": "3x-ui=sess-abc; Path=/; HttpOnly"},
        )
    )


def test_proxy_host_defaults_to_base_url_hostname():
    c = XuiClient(BASE, "u", "p")
    assert c.proxy_host == "panel.test"


def test_proxy_host_override_wins():
    c = XuiClient(BASE, "u", "p", proxy_host="node1.example.com")
    assert c.proxy_host == "node1.example.com"


@respx.mock
def test_login_failure_raises():
    _login_route(respx, ok=False)
    with pytest.raises(XuiError):
        XuiClient(BASE, "u", "p").login()


@respx.mock
def test_list_inbounds_returns_obj_list_and_logs_in_first():
    _login_route(respx)
    respx.get(f"{BASE}/panel/api/inbounds/list").mock(
        return_value=httpx.Response(
            200, json={"success": True, "msg": "", "obj": [{"id": 2, "port": 38695}]}
        )
    )
    c = XuiClient(BASE, "u", "p")
    got = c.list_inbounds()
    assert got == [{"id": 2, "port": 38695}]


@respx.mock
def test_create_socks_inbound_posts_mixed_and_returns_id():
    _login_route(respx)
    captured = {}

    def _add(request):
        captured["body"] = dict(httpx.QueryParams(request.content.decode()))
        return httpx.Response(
            200, json={"success": True, "msg": "created", "obj": {"id": 7, "port": 45000}}
        )

    respx.post(f"{BASE}/panel/api/inbounds/add").mock(side_effect=_add)
    c = XuiClient(BASE, "u", "p")
    iid = c.create_socks_inbound("alice", "pw123", 45000, 1785402000000, "reg:x@m.test")
    assert iid == 7
    body = captured["body"]
    assert body["protocol"] == "mixed"
    assert body["port"] == "45000"
    assert body["expiryTime"] == "1785402000000"
    assert body["remark"] == "reg:x@m.test"
    settings = json.loads(body["settings"])
    assert settings["auth"] == "password"
    assert settings["accounts"] == [{"user": "alice", "pass": "pw123"}]


@respx.mock
def test_create_failure_raises_xui_error():
    _login_route(respx)
    respx.post(f"{BASE}/panel/api/inbounds/add").mock(
        return_value=httpx.Response(
            200, json={"success": False, "msg": "port already in use", "obj": None}
        )
    )
    with pytest.raises(XuiError) as ei:
        XuiClient(BASE, "u", "p").create_socks_inbound("a", "b", 45000, 1, "r")
    assert "port" in str(ei.value)


@respx.mock
def test_delete_inbound_hits_del_path():
    _login_route(respx)
    route = respx.post(f"{BASE}/panel/api/inbounds/del/7").mock(
        return_value=httpx.Response(200, json={"success": True, "msg": "", "obj": 7})
    )
    XuiClient(BASE, "u", "p").delete_inbound(7)
    assert route.called


@respx.mock
def test_delete_inbound_swallows_api_failure():
    _login_route(respx)
    respx.post(f"{BASE}/panel/api/inbounds/del/7").mock(
        return_value=httpx.Response(
            200, json={"success": False, "msg": "not found", "obj": None}
        )
    )
    # 回收路径：API success=false 也不应抛出
    XuiClient(BASE, "u", "p").delete_inbound(7)


@respx.mock
def test_delete_inbound_swallows_transport_error():
    _login_route(respx)
    respx.post(f"{BASE}/panel/api/inbounds/del/7").mock(
        side_effect=httpx.ConnectError("boom")
    )
    # 回收路径：传输错误也不应抛出
    XuiClient(BASE, "u", "p").delete_inbound(7)


@respx.mock
def test_relogin_on_non_json_session_expiry():
    # 首次列表返回登录页 HTML（会话失效），应重登后重试成功
    login = _login_route(respx)
    calls = {"n": 0}

    def _list(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, text="<html>login</html>")
        return httpx.Response(200, json={"success": True, "msg": "", "obj": []})

    respx.get(f"{BASE}/panel/api/inbounds/list").mock(side_effect=_list)
    c = XuiClient(BASE, "u", "p")
    assert c.list_inbounds() == []
    assert login.call_count == 2  # 初次 + 会话失效重登
