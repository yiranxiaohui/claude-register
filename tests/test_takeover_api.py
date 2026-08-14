from fastapi.testclient import TestClient
from claude_register.anymail import AnyMailAccessError, ChildKey
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
            self.touches = 0
            self.relogin_calls = []
            self.relogin_error = None
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
        def touch(self):
            from server.takeover import TakeoverError
            if not self._running:
                raise TakeoverError("当前没有活动的接管会话")
            self.touches += 1
        def relogin(self, **kwargs):
            from server.takeover import TakeoverError
            self.relogin_calls.append(kwargs)
            if self.relogin_error:
                raise TakeoverError(self.relogin_error)
            return "sk-new"

    monkeypatch.setattr(deps, "TakeoverManager", FakeMgr)
    monkeypatch.setattr("server.app.check_session", lambda *a, **k: ("alive", "有效"))

    mail_control = {
        "invalid_keys": set(),
        "child": None,
        "checks": [],
        "creates": [],
    }

    class FakeMailClient:
        def __init__(self, *, base_url, api_key):
            self.base_url = base_url
            self.api_key = api_key

        def check_email_access(self, *, to):
            mail_control["checks"].append((self.base_url, self.api_key, to))
            if self.api_key in mail_control["invalid_keys"]:
                raise AnyMailAccessError(401, '{"error":"invalid or expired api key"}')

        def create_child_key(self, **kwargs):
            mail_control["creates"].append(kwargs)
            return mail_control["child"]

    monkeypatch.setattr("server.app.AnyMailClient", FakeMailClient)
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml",
                     now_fn=lambda: "2026-07-30T00:00:00Z")
    app.state.mail_control = mail_control
    return app


def _login(c):
    assert c.post("/api/login", json={"password": "pw"}).status_code == 200


def _seed_account(
    tmp_path,
    email,
    session_key="sk",
    proxy="",
    mail_key="",
    mail_base_url="",
):
    conn = db.init_db(tmp_path / "claude-register.db")
    db.upsert_account(conn, email, "x.com", None, "", None, "success",
                      session_key=session_key, proxy=proxy, created_at="t",
                      mail_key=mail_key, mail_base_url=mail_base_url)
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


def test_takeover_relogin_uses_account_mail_key_and_updates_session(tmp_path, monkeypatch):
    save_config(tmp_path / "config.yaml", {"panel_password": "pw"})
    _seed_account(
        tmp_path,
        "a@x.com",
        session_key="sk-old",
        mail_key="ak_child",
        mail_base_url="https://mail.test",
    )
    app = _client(tmp_path, monkeypatch)
    c = TestClient(app); _login(c)
    assert c.post("/api/takeover/start", json={"email": "a@x.com"}).status_code == 200

    r = c.post("/api/takeover/relogin")

    assert r.status_code == 200
    assert r.json() == {
        "ok": True,
        "email": "a@x.com",
        "check_status": "alive",
        "check_detail": "有效",
    }
    call = app.state.cr.takeover.relogin_calls[0]
    assert call["mail_api_key"] == "ak_child"
    assert call["mail_base_url"] == "https://mail.test"
    account = db.get_account(app.state.cr.conn, "a@x.com")
    assert account["session_key"] == "sk-new"
    assert account["check_status"] == "alive"


def test_takeover_relogin_falls_back_to_main_mail_credentials(tmp_path, monkeypatch):
    save_config(tmp_path / "config.yaml", {
        "panel_password": "pw",
        "anymail_api_key": "ak_main",
        "anymail_base_url": "https://main-mail.test",
    })
    _seed_account(tmp_path, "a@x.com")
    app = _client(tmp_path, monkeypatch)
    c = TestClient(app); _login(c)
    c.post("/api/takeover/start", json={"email": "a@x.com"})

    assert c.post("/api/takeover/relogin").status_code == 200
    call = app.state.cr.takeover.relogin_calls[0]
    assert call["mail_api_key"] == "ak_main"
    assert call["mail_base_url"] == "https://main-mail.test"


def test_takeover_relogin_repairs_invalid_child_key_permanently(tmp_path, monkeypatch):
    save_config(tmp_path / "config.yaml", {
        "panel_password": "pw",
        "anymail_api_key": "ak_main",
        "anymail_base_url": "https://main-mail.test",
    })
    _seed_account(
        tmp_path,
        "a@x.com",
        mail_key="ak_stale",
        mail_base_url="https://old-mail.test",
    )
    app = _client(tmp_path, monkeypatch)
    app.state.mail_control["invalid_keys"].add("ak_stale")
    app.state.mail_control["child"] = ChildKey(
        id="kid-new",
        plaintext="ak_repaired",
    )
    c = TestClient(app); _login(c)
    c.post("/api/takeover/start", json={"email": "a@x.com"})

    r = c.post("/api/takeover/relogin")

    assert r.status_code == 200
    assert app.state.mail_control["checks"] == [
        ("https://old-mail.test", "ak_stale", "a@x.com"),
        ("https://main-mail.test", "ak_main", "a@x.com"),
    ]
    assert app.state.mail_control["creates"] == [{
        "email": "a@x.com",
        "expires_at": None,
        "name_prefix": "claude-register-relogin",
    }]
    call = app.state.cr.takeover.relogin_calls[0]
    assert call["mail_api_key"] == "ak_repaired"
    assert call["mail_base_url"] == "https://main-mail.test"
    account = db.get_account(app.state.cr.conn, "a@x.com")
    assert account["mail_key"] == "ak_repaired"
    assert account["mail_base_url"] == "https://main-mail.test"


def test_takeover_relogin_clears_invalid_child_when_delegation_unavailable(
    tmp_path, monkeypatch
):
    save_config(tmp_path / "config.yaml", {
        "panel_password": "pw",
        "anymail_api_key": "ak_main",
        "anymail_base_url": "https://main-mail.test",
    })
    _seed_account(
        tmp_path,
        "a@x.com",
        mail_key="ak_stale",
        mail_base_url="https://old-mail.test",
    )
    app = _client(tmp_path, monkeypatch)
    app.state.mail_control["invalid_keys"].add("ak_stale")
    c = TestClient(app); _login(c)
    c.post("/api/takeover/start", json={"email": "a@x.com"})

    assert c.post("/api/takeover/relogin").status_code == 200

    call = app.state.cr.takeover.relogin_calls[0]
    assert call["mail_api_key"] == "ak_main"
    assert call["mail_base_url"] == "https://main-mail.test"
    account = db.get_account(app.state.cr.conn, "a@x.com")
    assert account["mail_key"] == ""
    assert account["mail_base_url"] == ""


def test_takeover_relogin_reports_invalid_main_key_during_repair(tmp_path, monkeypatch):
    save_config(tmp_path / "config.yaml", {
        "panel_password": "pw",
        "anymail_api_key": "ak_main_bad",
        "anymail_base_url": "https://main-mail.test",
    })
    _seed_account(
        tmp_path,
        "a@x.com",
        mail_key="ak_stale",
        mail_base_url="https://old-mail.test",
    )
    app = _client(tmp_path, monkeypatch)
    app.state.mail_control["invalid_keys"].update({"ak_stale", "ak_main_bad"})
    c = TestClient(app); _login(c)
    c.post("/api/takeover/start", json={"email": "a@x.com"})

    r = c.post("/api/takeover/relogin")

    assert r.status_code == 422
    assert "主凭据也无法读取邮箱" in r.json()["detail"]
    assert app.state.cr.takeover.relogin_calls == []


def test_takeover_relogin_without_active_session_returns_409(tmp_path, monkeypatch):
    save_config(tmp_path / "config.yaml", {"panel_password": "pw"})
    app = _client(tmp_path, monkeypatch)
    c = TestClient(app); _login(c)
    assert c.post("/api/takeover/relogin").status_code == 409


def test_takeover_relogin_failure_does_not_replace_session(tmp_path, monkeypatch):
    save_config(tmp_path / "config.yaml", {"panel_password": "pw"})
    _seed_account(
        tmp_path,
        "a@x.com",
        session_key="sk-old",
        mail_key="ak_child",
        mail_base_url="https://mail.test",
    )
    app = _client(tmp_path, monkeypatch)
    c = TestClient(app); _login(c)
    c.post("/api/takeover/start", json={"email": "a@x.com"})
    app.state.cr.takeover.relogin_error = "账号可能已被封"

    r = c.post("/api/takeover/relogin")

    assert r.status_code == 422
    assert r.json()["detail"] == "账号可能已被封"
    assert db.get_account(app.state.cr.conn, "a@x.com")["session_key"] == "sk-old"


def test_takeover_relogin_rejects_new_dead_session(tmp_path, monkeypatch):
    save_config(tmp_path / "config.yaml", {"panel_password": "pw"})
    _seed_account(
        tmp_path,
        "a@x.com",
        session_key="sk-old",
        mail_key="ak_child",
        mail_base_url="https://mail.test",
    )
    app = _client(tmp_path, monkeypatch)
    monkeypatch.setattr("server.app.check_session", lambda *a, **k: (
        "dead", "已失效（HTTP 403）",
    ))
    c = TestClient(app); _login(c)
    c.post("/api/takeover/start", json={"email": "a@x.com"})

    r = c.post("/api/takeover/relogin")

    assert r.status_code == 422
    assert "仍不可用" in r.json()["detail"]
    account = db.get_account(app.state.cr.conn, "a@x.com")
    assert account["session_key"] == "sk-old"
    assert account["check_status"] == "dead"


def test_takeover_stopped_on_server_shutdown(tmp_path, monkeypatch):
    save_config(tmp_path / "config.yaml", {"panel_password": "pw"})
    app = _client(tmp_path, monkeypatch)
    # TestClient 作上下文管理器：进入触发 startup，退出触发 shutdown。
    with TestClient(app) as c:
        _login(c)
        mgr = app.state.cr.takeover
    # 退出 with 后 shutdown 钩子应已调用兜底 stop()。
    assert mgr.stops >= 1


def test_vnc_auth_reuses_panel_cookie(tmp_path, monkeypatch):
    save_config(tmp_path / "config.yaml", {"panel_password": "pw"})
    app = _client(tmp_path, monkeypatch)
    c = TestClient(app)
    assert c.get("/api/vnc-auth").status_code == 401
    _login(c)
    assert c.get("/api/vnc-auth").status_code == 204


def test_takeover_heartbeat_requires_auth(tmp_path, monkeypatch):
    save_config(tmp_path / "config.yaml", {"panel_password": "pw"})
    app = _client(tmp_path, monkeypatch)
    c = TestClient(app)
    assert c.post("/api/takeover/heartbeat").status_code == 401


def test_takeover_heartbeat_renews_running_session(tmp_path, monkeypatch):
    save_config(tmp_path / "config.yaml", {"panel_password": "pw"})
    _seed_account(tmp_path, "a@x.com")
    app = _client(tmp_path, monkeypatch)
    c = TestClient(app); _login(c)
    assert c.post("/api/takeover/start", json={"email": "a@x.com"}).status_code == 200
    assert c.post("/api/takeover/heartbeat").json() == {"ok": True}
    assert app.state.cr.takeover.touches == 1


def test_takeover_heartbeat_without_running_session_returns_409(tmp_path, monkeypatch):
    save_config(tmp_path / "config.yaml", {"panel_password": "pw"})
    app = _client(tmp_path, monkeypatch)
    c = TestClient(app); _login(c)
    assert c.post("/api/takeover/heartbeat").status_code == 409
