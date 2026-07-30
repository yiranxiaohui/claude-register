"""环境变量解析。"""

from __future__ import annotations

import pytest

from claude_register.config import (
    DEFAULT_CODE_REGEX,
    DEFAULT_EXPIRES_HOURS,
    resolve_code_regex,
    resolve_expires_hours,
)


def test_default_expires_is_permanent():
    """默认有效期是永久(None)——注册成功的账号邮箱不能被 AnyMail cron 清掉。"""
    assert DEFAULT_EXPIRES_HOURS is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, None),
        ("", None),
        ("   ", None),
        ("48", 48.0),
        ("1.5", 1.5),
        ("0", None),
        ("-1", None),
    ],
)
def test_resolve_expires_hours(raw, expected):
    assert resolve_expires_hours(raw) == expected


def test_resolve_expires_hours_invalid_falls_back(capsys):
    """非数字用默认值(永久)，并且要提示用户，不能静默。"""
    assert resolve_expires_hours("abc") is DEFAULT_EXPIRES_HOURS is None
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


def test_fallback_regex_ignores_css_hex_colors():
    """实测过的坑：邮件 HTML 里的 #000000 / #737163 会被裸 \\d{6} 当成验证码。"""
    from claude_register.anymail import extract_code
    from claude_register.config import FALLBACK_CODE_REGEX

    html = '<td bgcolor="#000000" style="color: #737163;">Log in</td>'
    assert extract_code({"html_body": html}, FALLBACK_CODE_REGEX) is None


@pytest.mark.parametrize(
    "html",
    [
        'encoded\n<td bgcolor="#262624">',
        "unicode ... color:#737163",
        '.barcode{}<td bgcolor="#000000">',
    ],
)
def test_default_regex_ignores_css_hex_colors(html):
    """主正则实测坑：'code' 字样出现在 CSS 十六进制颜色前面时（跨行/跨标签也算），
    旧的 [^\\d]* 会一路匹配到 # 后面的 6 位十六进制数字，误当成验证码。
    #262624 是 Claude 品牌色，在真实的魔术链接邮件和 Welcome 邮件里都出现过。"""
    from claude_register.anymail import extract_code

    assert extract_code({"html_body": html}, DEFAULT_CODE_REGEX) is None
