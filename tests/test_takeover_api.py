from fastapi.testclient import TestClient
from server.app import create_app
from server.config_store import save_config
from server import db


def _client(tmp_path, monkeypatch):
    # 注入假 TakeoverManager，避免真起 Xvfb/浏览器
    import server.deps as deps

    class FakeMgr:
        def __init__(self, *a, **k):
            self._running = False
            self._email = None
            self.stops = 0
            self.web_port = 6901  # KasmVNC 反代路由会读它；测试可改指假上游
        def start(self, *, email, session_key, proxy="", idle_timeout_s=900):
            from server.takeover import TakeoverBusy
            if self._running:
                raise TakeoverBusy("busy")
            self._running = True; self._email = email
            return {"email": email, "started_at": "2026-07-30T00:00:00Z"}
        def stop(self):
            self.stops += 1
            self._running = False; self._email = None
        def status(self):
            return {"running": self._running, "email": self._email,
                    "started_at": "2026-07-30T00:00:00Z" if self._running else None}

    monkeypatch.setattr(deps, "TakeoverManager", FakeMgr)
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml",
                     now_fn=lambda: "2026-07-30T00:00:00Z")
    return app


def _login(c):
    assert c.post("/api/login", json={"password": "pw"}).status_code == 200


def _seed_account(tmp_path, email, session_key="sk", proxy=""):
    conn = db.init_db(tmp_path / "claude-register.db")
    db.upsert_account(conn, email, "x.com", None, "", None, "success",
                      session_key=session_key, proxy=proxy, created_at="t")
    conn.commit()


def test_takeover_requires_auth(tmp_path, monkeypatch):
    save_config(tmp_path / "config.yaml", {"panel_password": "pw"})
    app = _client(tmp_path, monkeypatch)
    c = TestClient(app)
    assert c.get("/api/takeover").status_code == 401
    assert c.post("/api/takeover/start", json={"email": "a@x.com"}).status_code == 401


def test_takeover_start_no_session_key_400(tmp_path, monkeypatch):
    save_config(tmp_path / "config.yaml", {"panel_password": "pw"})
    _seed_account(tmp_path, "a@x.com", session_key="")
    app = _client(tmp_path, monkeypatch)
    c = TestClient(app); _login(c)
    assert c.post("/api/takeover/start", json={"email": "a@x.com"}).status_code == 400


def test_takeover_start_unknown_account_404(tmp_path, monkeypatch):
    save_config(tmp_path / "config.yaml", {"panel_password": "pw"})
    app = _client(tmp_path, monkeypatch)
    c = TestClient(app); _login(c)
    assert c.post("/api/takeover/start", json={"email": "nobody@x.com"}).status_code == 404


def test_takeover_disabled_403(tmp_path, monkeypatch):
    save_config(tmp_path / "config.yaml", {"panel_password": "pw", "takeover_enabled": False})
    _seed_account(tmp_path, "a@x.com")
    app = _client(tmp_path, monkeypatch)
    c = TestClient(app); _login(c)
    assert c.post("/api/takeover/start", json={"email": "a@x.com"}).status_code == 403


def test_takeover_start_stop_status_flow(tmp_path, monkeypatch):
    save_config(tmp_path / "config.yaml", {"panel_password": "pw"})
    _seed_account(tmp_path, "a@x.com", session_key="sk", proxy="socks5://p:1")
    app = _client(tmp_path, monkeypatch)
    c = TestClient(app); _login(c)
    r = c.post("/api/takeover/start", json={"email": "a@x.com"})
    assert r.status_code == 200 and r.json()["email"] == "a@x.com"
    assert c.get("/api/takeover").json()["running"] is True
    assert c.post("/api/takeover/start", json={"email": "a@x.com"}).status_code == 409
    assert c.post("/api/takeover/stop").status_code == 200
    assert c.get("/api/takeover").json()["running"] is False


def test_takeover_stopped_on_server_shutdown(tmp_path, monkeypatch):
    save_config(tmp_path / "config.yaml", {"panel_password": "pw"})
    app = _client(tmp_path, monkeypatch)
    # TestClient 作上下文管理器：进入触发 startup，退出触发 shutdown。
    with TestClient(app) as c:
        _login(c)
        mgr = app.state.cr.takeover
    # 退出 with 后 shutdown 钩子应已调用兜底 stop()。
    assert mgr.stops >= 1


def test_vnc_http_requires_auth_and_502_without_upstream(tmp_path, monkeypatch):
    save_config(tmp_path / "config.yaml", {"panel_password": "pw"})
    app = _client(tmp_path, monkeypatch)
    c = TestClient(app)
    assert c.get("/vnc/").status_code == 401
    _login(c)
    # KasmVNC 没在跑（接管未启动）→ 明确 502 而不是挂起
    app.state.cr.takeover.web_port = 1  # 保证连不上
    assert c.get("/vnc/").status_code == 502


def test_vnc_http_proxies_to_kasm(tmp_path, monkeypatch):
    import http.server, threading
    save_config(tmp_path / "config.yaml", {"panel_password": "pw"})
    app = _client(tmp_path, monkeypatch)

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = f"kasm:{self.path}".encode()
            self.send_response(200)
            self.send_header("content-type", "text/html")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        app.state.cr.takeover.web_port = srv.server_address[1]
        c = TestClient(app); _login(c)
        r = c.get("/vnc/")
        assert r.status_code == 200 and r.text == "kasm:/"
        r = c.get("/vnc/assets/app.js")
        assert r.status_code == 200 and r.text == "kasm:/assets/app.js"
    finally:
        srv.shutdown()


def test_vnc_http_blocks_traversal(tmp_path, monkeypatch):
    save_config(tmp_path / "config.yaml", {"panel_password": "pw"})
    app = _client(tmp_path, monkeypatch)
    c = TestClient(app); _login(c)
    assert c.get("/vnc/..%2f..%2fetc%2fpasswd").status_code == 404


def test_vnc_ws_rejects_without_cookie(tmp_path, monkeypatch):
    import pytest
    from starlette.websockets import WebSocketDisconnect
    save_config(tmp_path / "config.yaml", {"panel_password": "pw"})
    app = _client(tmp_path, monkeypatch)
    c = TestClient(app)
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/vnc/websockify") as ws:
            ws.receive_bytes()


def test_vnc_ws_default_websockify_path(tmp_path, monkeypatch):
    """noVNC ≥1.5 忽略 URL path 参数、连默认 /websockify：必须命中 WS 桥而非 StaticFiles。"""
    import pytest
    from starlette.websockets import WebSocketDisconnect
    save_config(tmp_path / "config.yaml", {"panel_password": "pw"})
    app = _client(tmp_path, monkeypatch)
    c = TestClient(app)
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/websockify") as ws:
            ws.receive_bytes()
