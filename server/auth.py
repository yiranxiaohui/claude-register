"""密码登录：HMAC 签名 cookie。单用户。"""
from __future__ import annotations

import hashlib
import hmac

COOKIE_NAME = "cr_session"
_PAYLOAD = b"ok"


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
