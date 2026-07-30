from pathlib import Path
from server.config_store import load_config, save_config, to_redacted_dict, REDACTED


def test_load_missing_returns_defaults(tmp_path):
    cfg = load_config(tmp_path / "nope.yaml")
    assert cfg.panel_port == 8790
    assert cfg.anymail_expires_hours == 0.0  # 0 = 永久
    assert cfg.register_auto_login is True
    assert cfg.panel_password == ""


def test_save_then_load_roundtrip(tmp_path):
    p = tmp_path / "config.yaml"
    save_config(p, {"panel_password": "secret", "anymail_api_key": "ak_1",
                    "anymail_base_url": "https://mail.example.com"})
    cfg = load_config(p)
    assert cfg.panel_password == "secret"
    assert cfg.anymail_api_key == "ak_1"
    assert cfg.anymail_base_url == "https://mail.example.com"


def test_save_empty_password_keeps_existing(tmp_path):
    p = tmp_path / "config.yaml"
    save_config(p, {"panel_password": "secret"})
    save_config(p, {"panel_password": "", "anymail_domain": "example.com"})
    cfg = load_config(p)
    assert cfg.panel_password == "secret"
    assert cfg.anymail_domain == "example.com"


def test_redacted_hides_secrets(tmp_path):
    cfg = load_config(tmp_path / "nope.yaml")
    object.__setattr__(cfg, "panel_password", "secret")
    object.__setattr__(cfg, "anymail_api_key", "ak_1")
    d = to_redacted_dict(cfg)
    assert d["panel_password"] == REDACTED
    assert d["anymail_api_key"] == REDACTED
    assert d["panel_port"] == 8790


def test_register_proxy_roundtrip(tmp_path):
    path = tmp_path / "config.yaml"
    cfg = save_config(path, {"register_proxy": "http://user:pass@1.2.3.4:8080"})
    assert cfg.register_proxy == "http://user:pass@1.2.3.4:8080"
    assert load_config(path).register_proxy == "http://user:pass@1.2.3.4:8080"


def test_register_proxy_default_empty(tmp_path):
    assert load_config(tmp_path / "missing.yaml").register_proxy == ""


def test_register_proxy_not_redacted(tmp_path):
    path = tmp_path / "config.yaml"
    save_config(path, {"register_proxy": "http://user:pass@1.2.3.4:8080"})
    d = to_redacted_dict(load_config(path))
    assert d["register_proxy"] == "http://user:pass@1.2.3.4:8080"


def test_xui_defaults(tmp_path):
    cfg = load_config(tmp_path / "nope.yaml")
    assert cfg.xui_enabled is False
    assert cfg.xui_expiry_days == 30
    assert cfg.xui_port_min == 40000
    assert cfg.xui_port_max == 60000
    assert cfg.xui_nodes == ()


def test_xui_nodes_roundtrip(tmp_path):
    p = tmp_path / "config.yaml"
    node = {"name": "usa-4", "base_url": "https://usa-4.test:2053/xyz",
            "username": "u", "password": "secret", "proxy_host": "usa-4.example.com"}
    save_config(p, {"xui_enabled": True, "xui_expiry_days": 15,
                    "xui_port_min": 41000, "xui_port_max": 42000,
                    "xui_nodes": [node]})
    cfg = load_config(p)
    assert cfg.xui_enabled is True
    assert cfg.xui_expiry_days == 15
    assert cfg.xui_port_min == 41000
    assert cfg.xui_port_max == 42000
    assert len(cfg.xui_nodes) == 1
    assert cfg.xui_nodes[0]["name"] == "usa-4"
    assert cfg.xui_nodes[0]["password"] == "secret"
    assert cfg.xui_nodes[0]["proxy_host"] == "usa-4.example.com"


def test_xui_yaml_uses_port_range_list(tmp_path):
    p = tmp_path / "config.yaml"
    save_config(p, {"xui_port_min": 41000, "xui_port_max": 42000})
    raw = p.read_text(encoding="utf-8")
    assert "port_range" in raw  # 落盘为 [min, max] 而非两个散字段


def test_xui_node_password_redacted(tmp_path):
    p = tmp_path / "config.yaml"
    node = {"name": "usa-4", "base_url": "https://x", "username": "u",
            "password": "secret", "proxy_host": ""}
    save_config(p, {"xui_nodes": [node]})
    d = to_redacted_dict(load_config(p))
    assert d["xui_nodes"][0]["password"] == REDACTED
    assert d["xui_nodes"][0]["name"] == "usa-4"


def test_xui_node_blank_password_keeps_existing(tmp_path):
    p = tmp_path / "config.yaml"
    save_config(p, {"xui_nodes": [{"name": "usa-4", "base_url": "https://x",
                                   "username": "u", "password": "secret",
                                   "proxy_host": ""}]})
    # 二次保存：base_url 不变（同一节点），密码传 REDACTED（面板脱敏回传）→ 应沿用旧密码
    save_config(p, {"xui_nodes": [{"name": "usa-4", "base_url": "https://x",
                                   "username": "u2", "password": REDACTED,
                                   "proxy_host": ""}]})
    cfg = load_config(p)
    assert cfg.xui_nodes[0]["password"] == "secret"
    assert cfg.xui_nodes[0]["username"] == "u2"  # 其余字段照常更新


def test_xui_node_changed_base_url_does_not_carry_password(tmp_path):
    p = tmp_path / "config.yaml"
    save_config(p, {"xui_nodes": [{"name": "usa-4", "base_url": "https://x",
                                   "username": "u", "password": "secret",
                                   "proxy_host": ""}]})
    # 节点改指向另一个面板（base_url 变了），密码传 REDACTED → 不应沿用旧密码，
    # 因为这已经是"指向不同面板"，需要重新输入凭据。
    save_config(p, {"xui_nodes": [{"name": "usa-4", "base_url": "https://x2",
                                   "username": "u", "password": REDACTED,
                                   "proxy_host": ""}]})
    cfg = load_config(p)
    assert cfg.xui_nodes[0]["password"] == ""
    assert cfg.xui_nodes[0]["base_url"] == "https://x2"


def test_takeover_defaults(tmp_path):
    from server.config_store import load_config
    cfg = load_config(tmp_path / "nope.yaml")  # 文件不存在→默认值
    assert cfg.takeover_enabled is True
    assert cfg.takeover_idle_timeout_min == 15


def test_takeover_roundtrip(tmp_path):
    from server.config_store import load_config, save_config
    p = tmp_path / "config.yaml"
    save_config(p, {"takeover_enabled": False, "takeover_idle_timeout_min": 30})
    cfg = load_config(p)
    assert cfg.takeover_enabled is False
    assert cfg.takeover_idle_timeout_min == 30
    # 落盘结构在 takeover 段
    import yaml
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert raw["takeover"]["enabled"] is False
    assert raw["takeover"]["idle_timeout_min"] == 30
