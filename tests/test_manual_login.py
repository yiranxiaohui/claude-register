"""手动登录：未登录接管浏览器 → 用户自己登录 → 自动取 sk 新建/更新账号。"""
import threading

import pytest
from fastapi.testclient import TestClient

from server import db
from server.app import create_app
from server.config_store import save_config
from server.takeover import TakeoverError, TakeoverManager


class FakeProc:
    def __init__(self, argv):
        self.argv = argv

    def terminate(self):
        pass

    def kill(self):
        pass

    def wait(self, timeout=None):
        return 0


class FakeLauncher:
    def spawn(self, argv):
        return FakeProc(argv)


class FakeFitter:
    def stop(self):
        pass


class FakeBrowser:
    """模拟接管浏览器：cookie 由测试直接改，读取须在创建线程上。"""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.cookie = kwargs.get("session_key") or ""
        self.owner = threading.get_ident()
        self.closed = False

    def session_key(self):
        assert threading.get_ident() == self.owner
        return self.cookie

    def close(self):
        self.closed = True


def _manager(browsers):
    def browser_fn(**kwargs):
        b = FakeBrowser(**kwargs)
        browsers.append(b)
        return b

    return TakeoverManager(
        now_fn=lambda: "2026-07-30T00:00:00Z",
        launcher=FakeLauncher(),
        browser_fn=browser_fn,
        wait_display_fn=lambda display: None,
        wait_web_fn=lambda host, port: None,
        window_fitter_fn=lambda display: FakeFitter(),
    )


# ---------- TakeoverManager ----------

def test_manager_manual_mode_reads_cookie_and_tracks_saved_key():
    browsers = []
    m = _manager(browsers)
    m.start(email="", session_key="", proxy="socks5://p:1", idle_timeout_s=999,
            mode="manual", login_email="u@x.com")
    try:
        assert m.status()["mode"] == "manual"
        assert m.status()["email"] is None
        assert browsers[0].kwargs["login_email"] == "u@x.com"
        assert browsers[0].kwargs["session_key"] == ""
        assert m.read_session_key() == ""

        browsers[0].cookie = "sk-ant-sid01-new"
        assert m.read_session_key() == "sk-ant-sid01-new"
        m.mark_saved(email="u@x.com", session_key="sk-ant-sid01-new")
        ctx = m.capture_context()
        assert ctx["email"] == "u@x.com"
        assert ctx["saved_key"] == "sk-ant-sid01-new"
        assert ctx["proxy"] == "socks5://p:1"
        # 前端可见的 status 不得带代理（可能含账号密码）
        assert "proxy" not in m.status()
    finally:
        m.stop()
    assert m.status() == {"running": False, "email": None, "started_at": None, "mode": None}
    assert m.capture_context()["saved_key"] == ""


def test_manager_read_session_key_without_session_raises():
    m = _manager([])
    with pytest.raises(TakeoverError):
        m.read_session_key()


# ---------- API ----------

@pytest.fixture
def env(tmp_path, monkeypatch):
    save_config(tmp_path / "config.yaml", {
        "panel_password": "pw",
        "saved_proxies": [{"id": "p1", "name": "P1", "url": "socks5://u:p@1.2.3.4:1080"}],
    })
    browsers = []
    from server import deps
    monkeypatch.setattr(deps, "TakeoverManager", lambda now_fn: _manager(browsers))
    probes = []
    result = {"value": ("alive", "有效", "real@x.com")}

    def fake_probe(sk, proxy=None, *, want_email=False, **_):
        probes.append((sk, proxy, want_email))
        return result["value"]

    monkeypatch.setattr("server.app.probe_session", fake_probe)
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml",
                     now_fn=lambda: "2026-07-30T00:00:00Z")
    c = TestClient(app)
    assert c.post("/api/login", json={"password": "pw"}).status_code == 200
    return {"c": c, "app": app, "browsers": browsers, "probes": probes, "result": result}


def _conn(env):
    return env["app"].state.cr.conn


def test_manual_requires_auth(tmp_path):
    save_config(tmp_path / "config.yaml", {"panel_password": "pw"})
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml")
    c = TestClient(app)
    assert c.post("/api/takeover/manual", json={}).status_code == 401
    assert c.post("/api/takeover/capture").status_code == 401


def test_manual_rejects_bad_email_and_unknown_proxy(env):
    c = env["c"]
    assert c.post("/api/takeover/manual", json={"email": "not-an-email"}).status_code == 400
    assert c.post("/api/takeover/manual", json={"proxy_id": "nope"}).status_code == 400
    assert c.get("/api/takeover").json()["running"] is False


def test_manual_disabled_403(env, tmp_path):
    save_config(tmp_path / "config.yaml", {"takeover_enabled": False})
    assert env["c"].post("/api/takeover/manual", json={}).status_code == 403


def test_manual_login_creates_account_from_probed_email(env):
    c = env["c"]
    r = c.post("/api/takeover/manual", json={"email": " Typed@X.com ", "proxy_id": "p1"})
    assert r.status_code == 200
    assert r.json()["login_email"] == "typed@x.com"
    browser = env["browsers"][0]
    assert browser.kwargs["login_email"] == "typed@x.com"
    assert browser.kwargs["proxy"] == "socks5://u:p@1.2.3.4:1080"
    st = c.get("/api/takeover").json()
    assert st["running"] is True and st["mode"] == "manual" and st["email"] is None

    # 还没登录：无 Cookie，不检测、不入库
    r = c.post("/api/takeover/capture").json()
    assert r == {"found": False, "saved": False, "email": None}
    assert env["probes"] == []

    browser.cookie = "sk-ant-sid01-manual"
    r = c.post("/api/takeover/capture").json()
    assert r["saved"] is True and r["created"] is True
    assert r["email"] == "real@x.com"
    assert "real@x.com" in r["note"] and "typed@x.com" in r["note"]
    assert env["probes"] == [("sk-ant-sid01-manual", "socks5://u:p@1.2.3.4:1080", True)]
    row = db.get_account(_conn(env), "real@x.com")
    assert row["session_key"] == "sk-ant-sid01-manual"
    assert row["proxy"] == "socks5://u:p@1.2.3.4:1080"
    assert row["status"] == "success"
    assert row["check_status"] == "alive"
    # 会话归属切到新账号，之后可以「重新自动登录」
    assert c.get("/api/takeover").json()["email"] == "real@x.com"

    # 再轮询：sk 没变，不重复检测
    r = c.post("/api/takeover/capture").json()
    assert r["saved"] is True and r["unchanged"] is True
    assert len(env["probes"]) == 1


def test_manual_login_falls_back_to_typed_email_then_updates_existing(env):
    c = env["c"]
    conn = _conn(env)
    db.upsert_account(conn, "typed@x.com", "x.com", "", "", None, "success",
                      session_key="sk-old", created_at="t")
    env["result"]["value"] = ("error", "请求失败：Timeout", "")
    assert c.post("/api/takeover/manual", json={"email": "typed@x.com"}).status_code == 200
    env["browsers"][0].cookie = "sk-ant-sid01-fresh"
    r = c.post("/api/takeover/capture").json()
    assert r["saved"] is True and r["created"] is False
    assert r["email"] == "typed@x.com"
    assert r["check_status"] == "error"
    row = db.get_account(conn, "typed@x.com")
    assert row["session_key"] == "sk-ant-sid01-fresh"
    assert row["proxy"] == ""  # 直连时不覆盖原代理字段


def test_manual_login_without_email_uses_placeholder(env):
    c = env["c"]
    env["result"]["value"] = ("blocked", "Cloudflare 盾拦截，无法判定", "")
    assert c.post("/api/takeover/manual", json={}).status_code == 200
    env["browsers"][0].cookie = "sk-ant-sid01-anon"
    r = c.post("/api/takeover/capture").json()
    assert r["saved"] is True
    assert r["email"].endswith("@sk-import.local")
    assert "占位" in r["note"]


def test_dead_session_key_is_not_saved_or_reprobed(env):
    c = env["c"]
    env["result"]["value"] = ("dead", "已失效（HTTP 401）", "")
    assert c.post("/api/takeover/manual", json={"email": "u@x.com"}).status_code == 200
    env["browsers"][0].cookie = "sk-ant-sid01-dead"
    r = c.post("/api/takeover/capture").json()
    assert r["saved"] is False and r["check_status"] == "dead" and "repeat" not in r
    r = c.post("/api/takeover/capture").json()
    assert r["saved"] is False and r["repeat"] is True
    assert len(env["probes"]) == 1
    assert db.get_account(_conn(env), "u@x.com") is None


def test_capture_in_account_mode_updates_only_on_new_key(env):
    c = env["c"]
    conn = _conn(env)
    db.upsert_account(conn, "real@x.com", "x.com", "", "", None, "success",
                      session_key="sk-ant-sid01-old", created_at="t")
    assert c.post("/api/takeover/start", json={"email": "real@x.com"}).status_code == 200
    assert c.get("/api/takeover").json()["mode"] == "account"
    r = c.post("/api/takeover/capture").json()
    assert r["unchanged"] is True and env["probes"] == []

    env["browsers"][0].cookie = "sk-ant-sid01-relogged"
    r = c.post("/api/takeover/capture").json()
    assert r["saved"] is True and r["created"] is False and r["email"] == "real@x.com"
    assert db.get_account(conn, "real@x.com")["session_key"] == "sk-ant-sid01-relogged"


def test_capture_without_session_409(env):
    assert env["c"].post("/api/takeover/capture").status_code == 409


def test_manual_busy_409(env):
    c = env["c"]
    assert c.post("/api/takeover/manual", json={}).status_code == 200
    assert c.post("/api/takeover/manual", json={}).status_code == 409


def test_relogin_before_capture_in_manual_mode_409(env):
    c = env["c"]
    assert c.post("/api/takeover/manual", json={}).status_code == 200
    r = c.post("/api/takeover/relogin")
    assert r.status_code == 409
    assert "手动登录" in r.json()["detail"]
