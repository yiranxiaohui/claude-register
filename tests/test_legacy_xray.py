"""内嵌 Xray 移除后的注册代理迁移。"""
from __future__ import annotations

import json

from server.app import create_app
from server.config_store import load_config, save_config
from server.legacy_xray import LEGACY_XRAY_PROXY, migrate_register_proxy


def _write_xray(tmp_path, outbound):
    path = tmp_path / "xray" / "config.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"outbounds": [outbound]}), encoding="utf-8")


def _socks(address, port, users=None):
    server = {"address": address, "port": port}
    if users is not None:
        server["users"] = users
    return {"protocol": "socks", "settings": {"servers": [server]}, "tag": "proxy"}


def test_migrates_loopback_proxy_to_authenticated_upstream(tmp_path):
    cfg = tmp_path / "config.yaml"
    save_config(cfg, {"register_proxy": LEGACY_XRAY_PROXY})
    _write_xray(tmp_path, _socks("proxy.example", 1080, [{"user": "u@x", "pass": "p:w"}]))

    assert migrate_register_proxy(cfg, tmp_path) is True
    assert load_config(cfg).register_proxy == "socks5h://u%40x:p%3Aw@proxy.example:1080"


def test_migrates_ipv6_upstream_without_auth(tmp_path):
    cfg = tmp_path / "config.yaml"
    save_config(cfg, {"register_proxy": LEGACY_XRAY_PROXY})
    _write_xray(tmp_path, _socks("2001:db8::1", 1080))

    assert migrate_register_proxy(cfg, tmp_path) is True
    assert load_config(cfg).register_proxy == "socks5h://[2001:db8::1]:1080"


def test_leaves_other_proxies_untouched(tmp_path):
    cfg = tmp_path / "config.yaml"
    save_config(cfg, {"register_proxy": "socks5://other:1080"})
    _write_xray(tmp_path, _socks("proxy.example", 1080))

    assert migrate_register_proxy(cfg, tmp_path) is False
    assert load_config(cfg).register_proxy == "socks5://other:1080"


def test_keeps_loopback_when_upstream_is_missing_or_not_socks(tmp_path):
    cfg = tmp_path / "config.yaml"
    save_config(cfg, {"register_proxy": LEGACY_XRAY_PROXY})
    assert migrate_register_proxy(cfg, tmp_path) is False

    _write_xray(tmp_path, {"protocol": "vless", "settings": {"vnext": []}})
    assert migrate_register_proxy(cfg, tmp_path) is False
    assert load_config(cfg).register_proxy == LEGACY_XRAY_PROXY


def test_missing_config_is_noop(tmp_path):
    assert migrate_register_proxy(tmp_path / "config.yaml", tmp_path) is False
    assert not (tmp_path / "config.yaml").exists()


def test_app_startup_runs_migration(tmp_path):
    cfg = tmp_path / "config.yaml"
    save_config(cfg, {"register_proxy": LEGACY_XRAY_PROXY})
    _write_xray(tmp_path, _socks("proxy.example", 1080))

    create_app(data_dir=tmp_path, config_path=cfg)

    assert load_config(cfg).register_proxy == "socks5h://proxy.example:1080"
