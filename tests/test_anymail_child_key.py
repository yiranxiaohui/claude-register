"""create_child_key / delete_key:派生、降级、回收。"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

KEYS = "https://mail.test/api/keys"


def _key_response(**kw) -> dict:
    base = {
        "id": "kid-1",
        "name": "claude-register a@mail.test",
        "key_prefix": "ak_child12",
        "scopes": ["emails:read"],
        "provider": "domain",
        "address": "a@mail.test",
        "expires_at": "2026-07-31T00:00:00Z",
        "created_by_key_id": "parent-1",
    }
    base.update(kw)
    return {"ok": True, "key": base, "plaintext": "ak_child_secret"}


@respx.mock
def test_create_child_key_success_sends_narrowed_body(client):
    route = respx.post(KEYS).mock(
        return_value=httpx.Response(201, json=_key_response())
    )
    child = client.create_child_key(
        email="A@Mail.Test", expires_at="2026-07-31T00:00:00Z"
    )
    assert child is not None
    assert child.id == "kid-1"
    assert child.plaintext == "ak_child_secret"

    body = json.loads(route.calls.last.request.content)
    assert body["name"] == "claude-register a@mail.test"
    assert body["scopes"] == ["emails:read"]
    assert body["provider"] == "domain"
    assert body["address"] == "a@mail.test"  # 输入已规整为小写
    assert body["expires_at"] == "2026-07-31T00:00:00Z"
    assert route.calls.last.request.headers["Authorization"] == "Bearer ak_test"


@respx.mock
def test_create_child_key_forwards_null_expiry(client):
    """邮箱永久时 expires_at 传 null(由服务端按父 key 约束裁决)。"""
    route = respx.post(KEYS).mock(
        return_value=httpx.Response(201, json=_key_response(expires_at=None))
    )
    child = client.create_child_key(email="a@mail.test", expires_at=None)
    assert child is not None
    body = json.loads(route.calls.last.request.content)
    assert body["expires_at"] is None


@pytest.mark.parametrize("status", [400, 403, 500])
@respx.mock
def test_create_child_key_degrades_on_http_error(client, status):
    """403(缺 keys:create)/400(子集越界)/5xx:统一降级返回 None,不抛。"""
    respx.post(KEYS).mock(
        return_value=httpx.Response(status, json={"error": "nope"})
    )
    assert client.create_child_key(email="a@mail.test", expires_at=None) is None


@respx.mock
def test_create_child_key_degrades_on_network_error(client):
    respx.post(KEYS).mock(side_effect=httpx.ConnectError("boom"))
    assert client.create_child_key(email="a@mail.test", expires_at=None) is None


@respx.mock
def test_create_child_key_degrades_on_malformed_response(client):
    """200 但没有 plaintext/key.id:同样降级,绝不返回半残 ChildKey。"""
    respx.post(KEYS).mock(return_value=httpx.Response(201, json={"ok": True}))
    assert client.create_child_key(email="a@mail.test", expires_at=None) is None


def test_create_child_key_degrades_on_unexpected_exception(client, monkeypatch):
    """请求阶段抛出非 httpx.HTTPError 的异常(比如底层库/编码错误)也必须降级,
    而不是让 create_child_key 本身把注册流程带崩——契约是「任何失败都返回 None」。"""

    def boom(*a, **kw):
        raise ValueError("意外错误,不是 httpx.HTTPError")

    monkeypatch.setattr(httpx.Client, "post", boom)
    assert client.create_child_key(email="a@mail.test", expires_at=None) is None


@respx.mock
def test_create_child_key_malformed_response_log_redacts_plaintext(client):
    """响应异形但仍带了 plaintext(比如 key.id 缺失)时,日志不能把明文子 key 打出来。"""
    from claude_register import console

    respx.post(KEYS).mock(
        return_value=httpx.Response(
            201, json={"ok": True, "key": {}, "plaintext": "ak_leaked_secret"}
        )
    )
    captured: list[str] = []
    token = console.set_sink(captured.append)
    try:
        assert client.create_child_key(email="a@mail.test", expires_at=None) is None
    finally:
        console.reset_sink(token)

    joined = "\n".join(captured)
    assert "ak_leaked_secret" not in joined, f"日志泄露了子 key 明文:{joined}"
    assert "redacted" in joined


@respx.mock
def test_delete_key_200_and_404_silent(client):
    respx.delete(f"{KEYS}/kid-1").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    client.delete_key("kid-1")
    respx.delete(f"{KEYS}/kid-2").mock(
        return_value=httpx.Response(404, json={"error": "key not found"})
    )
    client.delete_key("kid-2")  # 幂等,不抛


@respx.mock
def test_delete_key_500_warns_not_raises(client):
    respx.delete(f"{KEYS}/kid-3").mock(
        return_value=httpx.Response(500, text="oops")
    )
    client.delete_key("kid-3")  # 只警告


def test_delete_key_empty_id_is_noop(client):
    # respx 未 mock 任何路由:若发了请求会直接报错,借此断言零请求
    client.delete_key("")
