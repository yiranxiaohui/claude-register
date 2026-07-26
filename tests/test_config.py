"""环境变量解析。"""

from __future__ import annotations

import pytest

from claude_register.config import (
    DEFAULT_CODE_REGEX,
    DEFAULT_EXPIRES_HOURS,
    resolve_code_regex,
    resolve_expires_hours,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, DEFAULT_EXPIRES_HOURS),
        ("", DEFAULT_EXPIRES_HOURS),
        ("   ", DEFAULT_EXPIRES_HOURS),
        ("48", 48.0),
        ("1.5", 1.5),
        ("0", None),
        ("-1", None),
    ],
)
def test_resolve_expires_hours(raw, expected):
    assert resolve_expires_hours(raw) == expected


def test_resolve_expires_hours_invalid_falls_back(capsys):
    """非数字用默认值，并且要提示用户，不能静默。"""
    assert resolve_expires_hours("abc") == DEFAULT_EXPIRES_HOURS
    assert "ANYMAIL_EXPIRES_HOURS" in capsys.readouterr().out


def test_resolve_code_regex_default():
    assert resolve_code_regex(None) == DEFAULT_CODE_REGEX
    assert resolve_code_regex("") == DEFAULT_CODE_REGEX


def test_resolve_code_regex_custom():
    assert resolve_code_regex(r"(\d{4})") == r"(\d{4})"


def test_resolve_code_regex_invalid_falls_back(capsys):
    """正则语法错时退回默认值并提示，不能让流程崩在这里。"""
    assert resolve_code_regex("(unclosed") == DEFAULT_CODE_REGEX
    assert "ANYMAIL_CODE_REGEX" in capsys.readouterr().out
