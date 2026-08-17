from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_xpra_is_proxied_directly_with_panel_auth():
    config = (ROOT / "deploy" / "nginx.conf").read_text(encoding="utf-8")

    assert "location /vnc/" in config
    assert "location = /websockify" not in config
    assert config.count("auth_request /_vnc_auth;") == 1
    assert "proxy_pass http://xpra/;" in config
    assert "proxy_read_timeout 86400s;" in config
    assert "proxy_socket_keepalive on;" in config
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


def test_runtime_installs_pinned_xpra_lts_and_html5_client():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    source = (ROOT / "deploy" / "xpra-lts.sources").read_text(encoding="utf-8")

    assert "XPRA_VERSION=5.1.6-r0-1" in dockerfile
    assert "XPRA_HTML5_VERSION=5.6-r14-1" in dockerfile
    assert "xpra-server=${XPRA_VERSION}" in dockerfile
    assert "xpra-html5=${XPRA_HTML5_VERSION}" in dockerfile
    assert "KASMVNC_VERSION" not in dockerfile
    assert "URIs: https://xpra.org/lts" in source


def test_takeover_wrapper_keeps_session_alive():
    page = (ROOT / "web" / "public" / "takeover.html").read_text(encoding="utf-8")

    assert 'src="/vnc/?reconnect=yes&amp;clipboard=yes&amp;keyboard=yes' in page
    assert 'allow="clipboard-read; clipboard-write"' in page
    assert "viewerHealthTimer = setInterval(recoverViewer, 5_000)" in page
    assert 'fetch("/api/takeover/heartbeat"' in page
    assert "setInterval(heartbeat, 20_000)" in page
