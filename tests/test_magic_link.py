"""魔术链接提取与轮询。"""

from __future__ import annotations

import httpx
import pytest
import respx

from claude_register.anymail import extract_magic_link

LATEST = "https://mail.test/api/emails/latest"

# 真实邮件片段（控制端从 AnyMail 实测抓取，链接在 html_body 里且被 HTML 转义）
REAL_HTML = (
    '<td align="center" bgcolor="#000000" role="presentation">'
    '<a href="https://claude.ai/magic-link#db3b0fc94f6475dbeae9b4d6ee1fea14:'
    'Y2xhdWRlX2FhZmRiZTI1QGNrdmxoai54eXo=" '
    'style="color: #737163;">Log in to Claude.ai</a></td>'
)
REAL_LINK = (
    "https://claude.ai/magic-link#db3b0fc94f6475dbeae9b4d6ee1fea14:"
    "Y2xhdWRlX2FhZmRiZTI1QGNrdmxoai54eXo="
)


def _email(**kw) -> dict:
    base = {"subject": "", "text_body": "", "html_body": "", "code": None}
    base.update(kw)
    return base


def test_extract_from_real_html_body():
    assert extract_magic_link(_email(html_body=REAL_HTML)) == REAL_LINK


def test_extract_handles_html_escaped_amp():
    """真实邮件里链接内部的字符可能被转义成数字实体（如 = 变成 &#61;），
    提取前必须 unescape，否则匹配会在实体处截断，取到的链接是不完整/错误的。"""
    # 把 REAL_LINK 结尾的 base64 padding "=" 换成它的数字实体 "&#61;"——
    # 转义发生在被正则匹配的 token 内部，而不是 token 之外，
    # 这样如果 extract_magic_link 漏调 unescape()，匹配会在 "&" 处截断，
    # 取到缺了结尾 "=" 的链接，断言就会失败。
    escaped_link = REAL_LINK[:-1] + "&#61;"
    assert escaped_link != REAL_LINK  # 确认确实做了转义替换
    html = f'<a href="{escaped_link}">Log in to Claude.ai</a>'
    assert extract_magic_link(_email(html_body=html)) == REAL_LINK


def test_extract_searches_text_body_too():
    assert extract_magic_link(_email(text_body=f"Click {REAL_LINK} to log in")) == REAL_LINK


def test_extract_returns_none_without_link():
    assert extract_magic_link(_email(html_body="<p>no link here</p>")) is None


def test_extract_ignores_other_claude_urls():
    """普通 claude.ai 链接不能误当成魔术链接。"""
    html = '<a href="https://claude.ai/login">Log in</a><a href="https://claude.ai/pricing">Pricing</a>'
    assert extract_magic_link(_email(html_body=html)) is None


def test_extract_recipient_from_link():
    """base64 尾巴解出收件邮箱——用来确认没抓错邮件。"""
    from claude_register.anymail import magic_link_recipient

    assert magic_link_recipient(REAL_LINK) == "claude_aafdbe25@ckvlhj.xyz"


def test_extract_recipient_handles_garbage():
    from claude_register.anymail import magic_link_recipient

    assert magic_link_recipient("https://claude.ai/magic-link#abc") is None


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@respx.mock
def test_poll_magic_link_hit_first_round(client):
    respx.get(LATEST).mock(
        return_value=httpx.Response(200, json={"emails": [_email(html_body=REAL_HTML)]})
    )
    clock = FakeClock()
    link = client.poll_magic_link(
        to="claude_aafdbe25@ckvlhj.xyz",
        since="2026-07-26T00:00:00Z",
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    assert link == REAL_LINK
    assert clock.slept == []


@respx.mock
def test_poll_magic_link_timeout_returns_none(client):
    respx.get(LATEST).mock(return_value=httpx.Response(200, json={"emails": []}))
    clock = FakeClock()
    link = client.poll_magic_link(
        to="a@mail.test",
        since="2026-07-26T00:00:00Z",
        timeout=10.0,
        interval=3.0,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    assert link is None
    assert clock.now >= 10.0


@respx.mock
def test_poll_magic_link_skips_wrong_recipient(client):
    """抓到别人的链接必须跳过，不能拿去登录错的账号。"""
    other = _email(
        html_body='<a href="https://claude.ai/magic-link#deadbeef:b3RoZXJAbWFpbC50ZXN0">x</a>'
    )
    respx.get(LATEST).mock(
        side_effect=[
            httpx.Response(200, json={"emails": [other]}),
            httpx.Response(200, json={"emails": [_email(html_body=REAL_HTML)]}),
        ]
    )
    clock = FakeClock()
    link = client.poll_magic_link(
        to="claude_aafdbe25@ckvlhj.xyz",
        since="2026-07-26T00:00:00Z",
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    assert link == REAL_LINK


@respx.mock
def test_poll_magic_link_skips_undecodable_recipient(client):
    """base64 尾巴解不出收件邮箱时必须当成没通过校验而跳过（fail closed），
    不能因为解不出来就当成"没问题"直接放行——否则可能登录到错的邮箱。"""
    undecodable = _email(
        html_body='<a href="https://claude.ai/magic-link#deadbeefdeadbeefdeadbeefdeadbeef">x</a>'
    )
    respx.get(LATEST).mock(return_value=httpx.Response(200, json={"emails": [undecodable]}))
    clock = FakeClock()
    link = client.poll_magic_link(
        to="claude_aafdbe25@ckvlhj.xyz",
        since="2026-07-26T00:00:00Z",
        timeout=10.0,
        interval=3.0,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    assert link is None
    assert clock.now >= 10.0


@respx.mock
def test_poll_magic_link_backoff_on_5xx(client):
    respx.get(LATEST).mock(
        side_effect=[
            httpx.Response(503, text="down"),
            httpx.Response(200, json={"emails": [_email(html_body=REAL_HTML)]}),
        ]
    )
    clock = FakeClock()
    link = client.poll_magic_link(
        to="claude_aafdbe25@ckvlhj.xyz",
        since="2026-07-26T00:00:00Z",
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    assert link == REAL_LINK
    assert clock.slept == [1.0]


@respx.mock
def test_poll_magic_link_sends_expected_params(client):
    """to/since 必须原样发给服务端——丢了 since 会静默拿到本次运行之前的旧链接，
    这正是 prepare_mailbox 和 test_mailbox.py 里那几个测试要守住的不变量。"""
    route = respx.get(LATEST).mock(
        return_value=httpx.Response(200, json={"emails": [_email(html_body=REAL_HTML)]})
    )
    client.poll_magic_link(
        to="claude_aafdbe25@ckvlhj.xyz",
        since="2026-07-26T00:00:00Z",
        sleep=lambda s: None,
        monotonic=FakeClock().monotonic,
    )
    params = route.calls[0].request.url.params
    assert params["to"] == "claude_aafdbe25@ckvlhj.xyz"
    assert params["since"] == "2026-07-26T00:00:00Z"
