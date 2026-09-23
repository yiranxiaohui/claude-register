"""密码登录：HMAC 签名 cookie。单用户。"""
from __future__ import annotations

import hashlib
import hmac

COOKIE_NAME = "cr_session"
_PAYLOAD = b"ok"
# 会话 cookie 存活时长（秒）。给浏览器一个明确过期时间，避免「永久会话」。
SESSION_MAX_AGE = 7 * 24 * 3600


def passwords_match(candidate, expected) -> bool:
    """常量时间比较面板密码，容忍任意输入类型与非 ASCII 字符。

    hmac.compare_digest 对 str 只支持 ASCII，直接传中文密码会抛 TypeError
    （表现为登录 500、账号被永久锁死）；统一编码成 bytes 再比。
    非字符串（null/数字/缺字段）一律判否，不让它冒泡成 500。
    """
    if not isinstance(candidate, str) or not isinstance(expected, str):
        return False
    if not expected:
        return False  # 未设置密码时不存在「正确密码」，不签发会话
    return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


def _key(password: str, secret: str) -> bytes:
    return hashlib.sha256(f"{secret}:{password}".encode()).digest()


def make_token(password: str, secret: str) -> str:
    sig = hmac.new(_key(password, secret), _PAYLOAD, hashlib.sha256).hexdigest()
    return sig


def verify_token(token: str, password: str, secret: str) -> bool:
    if not token or not password:
        return False
    expected = make_token(password, secret)
    return hmac.compare_digest(token, expected)
