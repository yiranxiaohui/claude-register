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
