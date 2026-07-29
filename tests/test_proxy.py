import pytest

from claude_register.browser import parse_proxy


def test_empty_means_direct():
    assert parse_proxy("") is None
    assert parse_proxy("   ") is None
    assert parse_proxy(None) is None


def test_http_no_auth():
    assert parse_proxy("http://1.2.3.4:8080") == {"server": "http://1.2.3.4:8080"}


def test_socks5_with_auth():
    assert parse_proxy("socks5://user:pass@proxy.example.com:1080") == {
        "server": "socks5://proxy.example.com:1080",
        "username": "user",
        "password": "pass",
    }


def test_auth_percent_decoded():
    assert parse_proxy("http://u%40x:p%23w@h:8080") == {
        "server": "http://h:8080",
        "username": "u@x",
        "password": "p#w",
    }


@pytest.mark.parametrize("bad", [
    "1.2.3.4:8080",          # 无 scheme
    "http://:8080",          # 无 host
    "http://host",           # 无 port
    "http://host:abc",       # 端口非数字
    "://",                   # 乱码
])
def test_invalid_raises(bad):
    with pytest.raises(ValueError):
        parse_proxy(bad)
