from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_kasm_is_proxied_directly_with_panel_auth():
    config = (ROOT / "deploy" / "nginx.conf").read_text(encoding="utf-8")

    assert "location /vnc/" in config
    assert "location = /websockify" in config
    assert config.count("auth_request /_vnc_auth;") == 2
    assert "proxy_pass http://kasmvnc/;" in config
    assert "proxy_pass http://kasmvnc/websockify;" in config
    assert "proxy_read_timeout 3600s;" in config
    assert "proxy_buffering off;" in config


def test_nginx_auth_subrequest_targets_fastapi():
    config = (ROOT / "deploy" / "nginx.conf").read_text(encoding="utf-8")

    assert "location = /_vnc_auth" in config
    assert "internal;" in config
    assert "proxy_pass http://panel/api/vnc-auth;" in config
    assert "proxy_set_header Cookie $http_cookie;" in config


def test_runtime_uses_supervisor_for_panel_and_nginx():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    supervisor = (ROOT / "deploy" / "supervisord.conf").read_text(encoding="utf-8")

    assert "nginx" in dockerfile
    assert "supervisor" in dockerfile
    assert "CLAUDE_REGISTER_INTERNAL_PORT=8791" in dockerfile
    assert "[program:panel]" in supervisor
    assert "[program:nginx]" in supervisor


def test_takeover_wrapper_keeps_session_alive():
    page = (ROOT / "web" / "public" / "takeover.html").read_text(encoding="utf-8")

    assert 'src="/vnc/?autoconnect=1&amp;resize=scale"' in page
    assert 'fetch("/api/takeover/heartbeat"' in page
    assert "setInterval(heartbeat, 20_000)" in page
