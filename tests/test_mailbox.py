"""选后缀、建邮箱、since 时序。"""

from __future__ import annotations

import json
import re

import httpx
import pytest
import respx

from claude_register import mailbox
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


def _make_fake_clock():
    """假时钟：每次调用产出严格递增、互不相同的刻度，并记录调用顺序。

    用来把「since 早于第一次账号请求」这个因果关系钉死——而不是靠
    `since <= 事后重新取的 now()` 这种无论顺序如何都成立的弱断言去推断。
    """
    ticks: list[str] = []

    def _clock() -> str:
        value = f"2026-07-26T00:00:{len(ticks):02d}Z"
        ticks.append(value)
        return value

    _clock.ticks = ticks
    return _clock


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
        return_value=httpx.Response(200, json=_account("qwertyuiop@only.test"))
    )
    box = create_for_suffix(client, "only.test")
    assert box.email.endswith("@only.test")
    sent = json.loads(route.calls[0].request.read().decode())
    local = sent["email"].split("@")[0]
    # 纯小写字母、无 claude 前缀，降低命中注册风控的概率
    assert re.fullmatch(r"[a-z]{8,}", local)
    assert "claude" not in local
    assert "expires_at" in json.dumps(sent)  # 默认 24 小时


@respx.mock
def test_create_for_suffix_retries_on_conflict(client):
    route = respx.post(ACCOUNTS).mock(
        side_effect=[
            httpx.Response(409, json={"error": "account already exists"}),
            httpx.Response(200, json=_account("secondname@only.test")),
        ]
    )
    box = create_for_suffix(client, "only.test")
    assert box.email == "secondname@only.test"
    first = json.loads(route.calls[0].request.read().decode())
    second = json.loads(route.calls[1].request.read().decode())
    assert first["email"] != second["email"]  # 必须换了本地部分
    retry_local = second["email"].split("@")[0]
    assert re.fullmatch(r"[a-z]{8,}", retry_local)


def test_random_local_is_pure_letters_and_random():
    from claude_register.anymail import random_local

    a, b = random_local(), random_local()
    assert re.fullmatch(r"[a-z]{8,}", a)
    assert a != b


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
def test_prepare_mailbox_records_since_before_create(client, monkeypatch):
    """接码文档 §8.2：since 必须早于 POST /api/accounts，
    否则会漏掉「建邮箱完成 → 首次轮询」窗口里到达的邮件。

    用严格递增、互不相同的假时钟把「先后」钉死，而不是靠一个
    恒真的 `since <= 事后重新取的 now()` 去推断——那种写法无论
    since 实际在哪一刻被取都会通过，测不出真实的时序回归。
    """
    fake_clock = _make_fake_clock()
    monkeypatch.setattr(mailbox, "utc_now_iso", fake_clock)

    request_ticks: list[str] = []

    def _capture(request):
        # 在请求真正发生的那一刻读一次时钟，作为「建邮箱时刻」的参照。
        request_ticks.append(fake_clock())
        return httpx.Response(200, json=_account("claude_x@mail.test"))

    respx.post(ACCOUNTS).mock(side_effect=_capture)

    box, since = prepare_mailbox(client, domain="mail.test")

    assert box.email == "claude_x@mail.test"
    assert since.endswith("Z")
    # since 必须是整个用例里第一次读到的刻度——即先于 POST 被记录。
    assert since == fake_clock.ticks[0]
    assert since < request_ticks[0]


@respx.mock
def test_prepare_mailbox_reuses_explicit_email(client, monkeypatch):
    """同样的时序不变量在「指定邮箱」分支上也必须成立：
    since 要早于 GET /api/accounts 那次查找请求。"""
    fake_clock = _make_fake_clock()
    monkeypatch.setattr(mailbox, "utc_now_iso", fake_clock)

    request_ticks: list[str] = []

    def _capture(request):
        request_ticks.append(fake_clock())
        return httpx.Response(
            200,
            json={
                "accounts": [
                    {"id": "old-1", "email": "old@mail.test", "expires_at": None}
                ]
            },
        )

    respx.get(ACCOUNTS).mock(side_effect=_capture)

    box, since = prepare_mailbox(client, email="Old@mail.test")
    assert box.email == "old@mail.test"
    assert box.id == "old-1"
    assert since.endswith("Z")
    assert since == fake_clock.ticks[0]
    assert since < request_ticks[0]


@respx.mock
def test_prepare_mailbox_creates_explicit_email_when_missing(client, monkeypatch):
    """指定邮箱不存在时会先 GET 查找、再 POST 创建——since 必须早于两者。"""
    fake_clock = _make_fake_clock()
    monkeypatch.setattr(mailbox, "utc_now_iso", fake_clock)

    request_ticks: list[str] = []

    def _capture_get(request):
        request_ticks.append(fake_clock())
        return httpx.Response(200, json={"accounts": []})

    def _capture_post(request):
        request_ticks.append(fake_clock())
        return httpx.Response(200, json=_account("brand@mail.test"))

    respx.get(ACCOUNTS).mock(side_effect=_capture_get)
    respx.post(ACCOUNTS).mock(side_effect=_capture_post)

    box, since = prepare_mailbox(client, email="brand@mail.test")
    assert box.email == "brand@mail.test"
    assert since == fake_clock.ticks[0]
    assert request_ticks == fake_clock.ticks[1:]
    assert all(since < tick for tick in request_ticks)
