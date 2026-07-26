"""pytest 共享 fixture。"""

from __future__ import annotations

import pytest

from claude_register import anymail

BASE_URL = "https://mail.test"


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch):
    """禁止测试读取真实 .env。"""
    monkeypatch.setattr(anymail, "load_dotenv", lambda *a, **k: None)


@pytest.fixture
def client():
    return anymail.AnyMailClient(
        base_url=BASE_URL,
        api_key="ak_test",
        domain="mail.test",
    )
