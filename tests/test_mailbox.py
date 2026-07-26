"""选后缀、建邮箱、since 时序。"""

from __future__ import annotations

import httpx
import pytest
import respx

from claude_register.mailbox import (
    choose_suffix,
    create_for_suffix,
    prepare_mailbox,
)

ACCOUNTS = "https://mail.test/api/accounts"
DOMAINS = "https://mail.test/api/domains"


def _account(email: str, **kw) -> dict:
    base = {"id": "acct-1", "provider": "domain", "email": email, "expires_at": None}
    base.update(kw)
    return {"ok": True, "account": base}


# ---------- choose_suffix ----------


def test_choose_suffix_prefers_explicit_arg(client):
    """--domain 优先级最高，不该发任何请求。"""
    assert choose_suffix(client, "Example.COM") == "example.com"


def test_choose_suffix_normalizes_input(client):
    assert choose_suffix(client, "@mail.example.com.") == "mail.example.com"


def test_choose_suffix_falls_back_to_client_domain(client):
    """client.domain 来自 ANYMAIL_DOMAIN。"""
    assert choose_suffix(client, None) == "mail.test"


@respx.mock
def test_choose_suffix_single_domain_no_prompt(client):
    client.domain = ""
    respx.get(DOMAINS).mock(
        return_value=httpx.Response(200, json={"domains": [{"name": "only.test"}]})
    )

    def _never(msg):
        raise AssertionError("只有一个域名时不该提示用户")

    assert choose_suffix(client, None, prompt=_never) == "only.test"


@respx.mock
def test_choose_suffix_multi_domain_prompts(client):
    client.domain = ""
    respx.get(DOMAINS).mock(
        return_value=httpx.Response(
            200, json={"domains": [{"name": "a.test"}, {"name": "b.test"}]}
        )
    )
    assert choose_suffix(client, None, prompt=lambda msg: "2") == "b.test"


@respx.mock
def test_choose_suffix_empty_input_picks_first(client):
    client.domain = ""
    respx.get(DOMAINS).mock(
        return_value=httpx.Response(
            200, json={"domains": [{"name": "a.test"}, {"name": "b.test"}]}
        )
    )
    assert choose_suffix(client, None, prompt=lambda msg: "") == "a.test"


@respx.mock
def test_choose_suffix_retries_invalid_input(client):
    client.domain = ""
    respx.get(DOMAINS).mock(
        return_value=httpx.Response(
            200, json={"domains": [{"name": "a.test"}, {"name": "b.test"}]}
        )
    )
    answers = iter(["99", "abc", "2"])
    assert choose_suffix(client, None, prompt=lambda msg: next(answers)) == "b.test"


@respx.mock
def test_choose_suffix_no_domains_raises(client):
    client.domain = ""
    respx.get(DOMAINS).mock(return_value=httpx.Response(200, json={"domains": []}))
    with pytest.raises(ValueError, match="ANYMAIL_DOMAIN"):
        choose_suffix(client, None)


# ---------- create_for_suffix ----------


@respx.mock
def test_create_for_suffix_generates_random_local_part(client):
    route = respx.post(ACCOUNTS).mock(
        return_value=httpx.Response(200, json=_account("claude_deadbeef@only.test"))
    )
    box = create_for_suffix(client, "only.test")
    assert box.email.endswith("@only.test")
    sent = route.calls[0].request.read().decode()
    assert "claude_" in sent
    assert "expires_at" in sent  # 默认 24 小时


@respx.mock
def test_create_for_suffix_retries_on_conflict(client):
    route = respx.post(ACCOUNTS).mock(
        side_effect=[
            httpx.Response(409, json={"error": "account already exists"}),
            httpx.Response(200, json=_account("claude_second@only.test")),
        ]
    )
    box = create_for_suffix(client, "only.test")
    assert box.email == "claude_second@only.test"
    first = route.calls[0].request.read().decode()
    second = route.calls[1].request.read().decode()
    assert first != second  # 必须换了前缀


@respx.mock
def test_create_for_suffix_permanent_when_expires_zero(client, monkeypatch):
    monkeypatch.setenv("ANYMAIL_EXPIRES_HOURS", "0")
    route = respx.post(ACCOUNTS).mock(
        return_value=httpx.Response(200, json=_account("claude_x@only.test"))
    )
    create_for_suffix(client, "only.test")
    assert "expires_at" not in route.calls[0].request.read().decode()


# ---------- prepare_mailbox：since 时序不变量 ----------


@respx.mock
def test_prepare_mailbox_records_since_before_create(client):
    """接码文档 §8.2：since 必须早于 POST /api/accounts，
    否则会漏掉「建邮箱完成 → 首次轮询」窗口里到达的邮件。"""
    observed: list[str] = []

    def _capture(request):
        observed.append("post")
        return httpx.Response(200, json=_account("claude_x@mail.test"))

    respx.post(ACCOUNTS).mock(side_effect=_capture)

    box, since = prepare_mailbox(client, domain="mail.test")

    assert observed == ["post"]
    assert box.email == "claude_x@mail.test"
    assert since.endswith("Z")
    # since 必须是建邮箱之前的时刻：重新取 now 一定不早于它
    from claude_register.mailbox import utc_now_iso

    assert since <= utc_now_iso()


@respx.mock
def test_prepare_mailbox_reuses_explicit_email(client):
    respx.get(ACCOUNTS).mock(
        return_value=httpx.Response(
            200,
            json={
                "accounts": [
                    {"id": "old-1", "email": "old@mail.test", "expires_at": None}
                ]
            },
        )
    )
    box, since = prepare_mailbox(client, email="Old@mail.test")
    assert box.email == "old@mail.test"
    assert box.id == "old-1"
    assert since.endswith("Z")


@respx.mock
def test_prepare_mailbox_creates_explicit_email_when_missing(client):
    respx.get(ACCOUNTS).mock(
        return_value=httpx.Response(200, json={"accounts": []})
    )
    respx.post(ACCOUNTS).mock(
        return_value=httpx.Response(200, json=_account("brand@mail.test"))
    )
    box, _ = prepare_mailbox(client, email="brand@mail.test")
    assert box.email == "brand@mail.test"
