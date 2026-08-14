"""接码轮询。"""

from __future__ import annotations

import httpx
import pytest
import respx

from claude_register.anymail import AnyMailAccessError, extract_code
from claude_register.config import DEFAULT_CODE_REGEX, FALLBACK_CODE_REGEX

LATEST = "https://mail.test/api/emails/latest"


class FakeClock:
    """假时钟：sleep 只推进时间，不真等。"""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _email(**kw) -> dict:
    base = {
        "subject": "",
        "text_body": "",
        "html_body": "",
        "code": None,
    }
    base.update(kw)
    return base


# ---------- extract_code ----------


def test_extract_code_uses_capture_group():
    email = _email(text_body="Your login code is 123456")
    assert extract_code(email, DEFAULT_CODE_REGEX) == "123456"


def test_extract_code_default_regex_ignores_dates():
    """裸 \\d{6} 会误取日期，主正则必须要求 'code' 字样。"""
    email = _email(text_body="Sent on 2026-07-26 at 123456 UTC")
    assert extract_code(email, DEFAULT_CODE_REGEX) is None


def test_extract_code_prefers_code_over_date():
    email = _email(text_body="Your code is 483920. Sent 2026-07-26.")
    assert extract_code(email, DEFAULT_CODE_REGEX) == "483920"


def test_extract_code_searches_html_and_subject():
    assert extract_code(_email(subject="code 111111"), DEFAULT_CODE_REGEX) == "111111"
    assert extract_code(
        _email(html_body="<b>code</b>: 222222"), DEFAULT_CODE_REGEX
    ) == "222222"


def test_extract_code_fallback_regex():
    email = _email(text_body="Verification: 654321")
    assert extract_code(email, DEFAULT_CODE_REGEX) is None
    assert extract_code(email, FALLBACK_CODE_REGEX) == "654321"


def test_extract_code_fallback_ignores_long_digit_runs():
    """\\b 边界保证不会从 9 位数里截 6 位。"""
    assert extract_code(_email(text_body="id 123456789"), FALLBACK_CODE_REGEX) is None


def test_extract_code_no_match():
    assert extract_code(_email(text_body="no digits here"), DEFAULT_CODE_REGEX) is None


# ---------- poll_code ----------


@respx.mock
def test_poll_code_hit_first_round(client):
    respx.get(LATEST).mock(
        return_value=httpx.Response(200, json={"emails": [_email(code="384729")]})
    )
    clock = FakeClock()
    code = client.poll_code(
        to="a@mail.test",
        since="2026-07-26T00:00:00Z",
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    assert code == "384729"
    assert clock.slept == []  # 首轮命中不该睡


@respx.mock
def test_poll_code_hit_third_round(client):
    respx.get(LATEST).mock(
        side_effect=[
            httpx.Response(200, json={"emails": []}),
            httpx.Response(200, json={"emails": []}),
            httpx.Response(200, json={"emails": [_email(code="112233")]}),
        ]
    )
    clock = FakeClock()
    code = client.poll_code(
        to="a@mail.test",
        since="2026-07-26T00:00:00Z",
        interval=3.0,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    assert code == "112233"
    assert clock.slept == [3.0, 3.0]


@respx.mock
def test_poll_code_timeout_returns_none(client):
    respx.get(LATEST).mock(return_value=httpx.Response(200, json={"emails": []}))
    clock = FakeClock()
    code = client.poll_code(
        to="a@mail.test",
        since="2026-07-26T00:00:00Z",
        timeout=10.0,
        interval=3.0,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    assert code is None
    assert clock.now >= 10.0


@respx.mock
def test_poll_code_client_side_fallback(client):
    """服务端 code 为 null，但正文里有 6 位数 —— 同一次响应里用兜底正则接手，
    不再多发一次请求。"""
    respx.get(LATEST).mock(
        return_value=httpx.Response(
            200,
            json={"emails": [_email(code=None, text_body="Verification: 998877")]},
        )
    )
    clock = FakeClock()
    code = client.poll_code(
        to="a@mail.test",
        since="2026-07-26T00:00:00Z",
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    assert code == "998877"
    assert respx.calls.call_count == 1  # 关键：没有翻倍请求


@respx.mock
def test_poll_code_sends_expected_params(client):
    route = respx.get(LATEST).mock(
        return_value=httpx.Response(200, json={"emails": [_email(code="1")]})
    )
    client.poll_code(
        to="a@mail.test",
        since="2026-07-26T00:00:00Z",
        sleep=lambda s: None,
        monotonic=FakeClock().monotonic,
    )
    params = route.calls[0].request.url.params
    assert params["to"] == "a@mail.test"
    assert params["since"] == "2026-07-26T00:00:00Z"
    assert params["code_regex"] == DEFAULT_CODE_REGEX


@respx.mock
def test_poll_code_backoff_on_5xx_then_recovers(client):
    respx.get(LATEST).mock(
        side_effect=[
            httpx.Response(503, text="upstream down"),
            httpx.Response(503, text="upstream down"),
            httpx.Response(200, json={"emails": [_email(code="445566")]}),
        ]
    )
    clock = FakeClock()
    code = client.poll_code(
        to="a@mail.test",
        since="2026-07-26T00:00:00Z",
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    assert code == "445566"
    assert clock.slept == [1.0, 2.0]  # 指数退避 1s → 2s


@respx.mock
def test_poll_code_backoff_on_network_error(client):
    respx.get(LATEST).mock(
        side_effect=[
            httpx.ConnectError("boom"),
            httpx.Response(200, json={"emails": [_email(code="778899")]}),
        ]
    )
    clock = FakeClock()
    code = client.poll_code(
        to="a@mail.test",
        since="2026-07-26T00:00:00Z",
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    assert code == "778899"
    assert clock.slept == [1.0]


@respx.mock
def test_poll_code_backoff_resets_after_success(client):
    """失败 → 成功（空结果）→ 失败：退避计数应在成功后归零，而不是继续翻倍。"""
    respx.get(LATEST).mock(
        side_effect=[
            httpx.Response(503, text="upstream down"),
            httpx.Response(200, json={"emails": []}),
            httpx.Response(503, text="upstream down"),
            httpx.Response(200, json={"emails": [_email(code="556677")]}),
        ]
    )
    clock = FakeClock()
    code = client.poll_code(
        to="a@mail.test",
        since="2026-07-26T00:00:00Z",
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    assert code == "556677"
    # 3.0 是成功空轮之后的正常轮询间隔；末尾的 1.0 证明退避已归零，而非继续翻倍到 2.0
    assert clock.slept == [1.0, 3.0, 1.0]


@respx.mock
def test_poll_code_backoff_on_malformed_json_body(client):
    """200 但响应体不是合法 JSON（比如网关错误页）：应按退避重试，
    不能当作致命错误、更不能让异常逃出轮询循环。"""
    respx.get(LATEST).mock(
        side_effect=[
            httpx.Response(200, text="<html>Bad Gateway</html>"),
            httpx.Response(200, json={"emails": [_email(code="334455")]}),
        ]
    )
    clock = FakeClock()
    code = client.poll_code(
        to="a@mail.test",
        since="2026-07-26T00:00:00Z",
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    assert code == "334455"
    assert clock.slept == [1.0]


@respx.mock
def test_poll_code_backoff_caps_at_4s(client):
    respx.get(LATEST).mock(return_value=httpx.Response(503, text="down"))
    clock = FakeClock()
    client.poll_code(
        to="a@mail.test",
        since="2026-07-26T00:00:00Z",
        timeout=30.0,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    assert max(clock.slept) == 4.0


@respx.mock
@pytest.mark.parametrize("status", [400, 401, 403])
def test_poll_code_fatal_errors_raise_immediately(client, status):
    """scope 不足 / key 失效 / 正则语法错都不会自己好，必须立刻抛出。"""
    respx.get(LATEST).mock(
        return_value=httpx.Response(status, text='{"error":"missing required scope"}')
    )
    clock = FakeClock()
    with pytest.raises(RuntimeError, match="missing required scope"):
        client.poll_code(
            to="a@mail.test",
            since="2026-07-26T00:00:00Z",
            sleep=clock.sleep,
            monotonic=clock.monotonic,
        )
    assert clock.slept == []  # 不该退避重试


@respx.mock
@pytest.mark.parametrize("status", [401, 403])
def test_check_email_access_raises_typed_credential_error(client, status):
    route = respx.get(LATEST).mock(
        return_value=httpx.Response(status, json={"error": "credential rejected"})
    )

    with pytest.raises(AnyMailAccessError) as exc_info:
        client.check_email_access(to="a@mail.test")

    assert exc_info.value.status_code == status
    params = route.calls.last.request.url.params
    assert params["to"] == "a@mail.test"
    assert params["code_regex"] == ""
    assert params["limit"] == "1"
