"""server.app 集成测试：登录、脱敏配置、鉴权、忙时 409。"""
from __future__ import annotations

import time

from fastapi.testclient import TestClient

from server.app import create_app
from server.config_store import save_config


def _client(tmp_path):
    app = create_app(
        data_dir=tmp_path,
        config_path=tmp_path / "config.yaml",
        now_fn=lambda: "2026-07-29T00:00:00Z",
    )
    return TestClient(app)


def test_login_wrong_password(tmp_path):
    save_config(tmp_path / "config.yaml", {"panel_password": "pw"})
    c = _client(tmp_path)
    r = c.post("/api/login", json={"password": "nope"})
    assert r.status_code == 401


def test_login_and_get_config_redacted(tmp_path):
    save_config(tmp_path / "config.yaml", {"panel_password": "pw", "anymail_api_key": "ak_1"})
    c = _client(tmp_path)
    assert c.post("/api/login", json={"password": "pw"}).status_code == 200
    r = c.get("/api/config")
    assert r.status_code == 200
    assert r.json()["anymail_api_key"] == "••••"


def test_runs_requires_auth(tmp_path):
    save_config(tmp_path / "config.yaml", {"panel_password": "pw"})
    c = _client(tmp_path)
    assert c.get("/api/runs").status_code == 401


def test_start_run_busy_returns_409(tmp_path, monkeypatch):
    save_config(
        tmp_path / "config.yaml",
        {"panel_password": "pw", "anymail_api_key": "ak", "anymail_base_url": "https://m"},
    )
    c = _client(tmp_path)
    c.post("/api/login", json={"password": "pw"})

    import claude_register.flow as flow

    monkeypatch.setattr(flow, "run", lambda **kw: time.sleep(0.5))

    r1 = c.post("/api/runs", json={"email": "a@x.com"})
    assert r1.status_code == 200
    r2 = c.post("/api/runs", json={"email": "b@x.com"})
    assert r2.status_code == 409


def _wait_idle(client, run_id, timeout=5.0):
    """轮询 run 详情直到 status 不再是 running。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/runs/{run_id}")
        if r.json()["status"] != "running":
            return r.json()
        time.sleep(0.02)
    raise AssertionError("run 未在超时内结束")


def test_stream_no_duplicate_lines(tmp_path, monkeypatch):
    save_config(tmp_path / "config.yaml", {"panel_password": "pw"})
    c = _client(tmp_path)
    c.post("/api/login", json={"password": "pw"})

    import claude_register.flow as flow
    from claude_register import console

    def fake_flow(**kw):
        console.log("LINE_ONE")
        console.log("LINE_TWO")

    monkeypatch.setattr(flow, "run", fake_flow)

    rid = c.post("/api/runs", json={"email": "a@x.com"}).json()["run_id"]
    # 等 run 结束，走 finished（文件补发）路径——TestClient 同步 SSE 下最稳定。
    detail = _wait_idle(c, rid)
    assert detail["status"] == "success"

    with c.stream("GET", f"/api/runs/{rid}/stream") as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())

    # 每行恰好出现一次，且有 done 事件。
    assert body.count("LINE_ONE") == 1
    assert body.count("LINE_TWO") == 1
    assert "event: done" in body


def test_stream_active_run_no_duplicate_over_http(tmp_path, monkeypatch):
    """真实回归：驱动 /api/runs/{id}/stream 面对 ACTIVE run，断言不重复。

    旧 gen()（先补发 log.txt 再排空队列）会让两行各出现两次——本测试对旧代码
    会失败（count==2），对新代码通过（count==1）。
    """
    import threading

    save_config(tmp_path / "config.yaml", {"panel_password": "pw"})
    c = _client(tmp_path)
    c.post("/api/login", json={"password": "pw"})

    import claude_register.flow as flow
    from claude_register import console

    release = threading.Event()

    def fake_flow(**kw):
        console.log("LINE_ONE")
        console.log("LINE_TWO")
        # 阻塞让 run 保持活动，直到测试收齐两行日志后放行。
        release.wait(timeout=10)

    monkeypatch.setattr(flow, "run", fake_flow)

    rid = c.post("/api/runs", json={"email": "a@x.com"}).json()["run_id"]

    try:
        # 等两行都落到 log.txt，确保队列已累积这两行（旧 bug 的触发前提）。
        run_dir = tmp_path / "runs" / str(rid)
        deadline = time.time() + 10
        while time.time() < deadline:
            lp = run_dir / "log.txt"
            if lp.exists() and "LINE_ONE" in lp.read_text() and "LINE_TWO" in lp.read_text():
                break
            time.sleep(0.02)
        else:
            raise AssertionError("两行日志未在超时内写入")
        # 此刻 run 仍活动（fake_flow 阻塞在 release 上）。
        assert c.get(f"/api/runs/{rid}").json()["status"] == "running"

        log_events: list[str] = []
        done_seen = False
        cur_event = None
        with c.stream("GET", f"/api/runs/{rid}/stream") as resp:
            assert resp.status_code == 200
            for raw in resp.iter_lines():
                line = raw.decode() if isinstance(raw, bytes) else raw
                if line.startswith("event:"):
                    cur_event = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    data = line.split(":", 1)[1].strip()
                    if cur_event == "log":
                        log_events.append(data)
                        # 收齐两行后放行 fake_flow，触发 done。
                        if release is not None and not release.is_set() and \
                                log_events.count("LINE_ONE") and log_events.count("LINE_TWO"):
                            release.set()
                    elif cur_event == "done":
                        done_seen = True
                        break
    finally:
        release.set()
        _wait_idle(c, rid)

    assert log_events.count("LINE_ONE") == 1, log_events
    assert log_events.count("LINE_TWO") == 1, log_events
    assert done_seen


def test_runs_artifact_requires_auth(tmp_path):
    save_config(tmp_path / "config.yaml", {"panel_password": "pw"})
    c = _client(tmp_path)
    run_dir = tmp_path / "runs" / "7"
    run_dir.mkdir(parents=True)
    (run_dir / "log.txt").write_text("SECRET_MAGIC_LINK", encoding="utf-8")

    # 无 cookie → 401，不泄露
    r = c.get("/runs/7/log.txt")
    assert r.status_code == 401
    assert "SECRET_MAGIC_LINK" not in r.text

    # 登录后 → 200 且返回内容
    c.post("/api/login", json={"password": "pw"})
    r2 = c.get("/runs/7/log.txt")
    assert r2.status_code == 200
    assert r2.text == "SECRET_MAGIC_LINK"


def test_runs_artifact_path_traversal_blocked(tmp_path):
    save_config(tmp_path / "config.yaml", {"panel_password": "pw", "anymail_api_key": "ak_secret"})
    c = _client(tmp_path)
    c.post("/api/login", json={"password": "pw"})
    (tmp_path / "runs" / "1").mkdir(parents=True)

    for evil in ("..%2f..%2fconfig.yaml", "../../config.yaml", "..%2fsecret.key"):
        r = c.get(f"/runs/1/{evil}")
        assert r.status_code == 404, evil
        assert "ak_secret" not in r.text
        assert "panel" not in r.text


def test_bootstrap_empty_password_blocks_runs(tmp_path):
    # 空密码引导态
    c = _client(tmp_path)
    # 配置可读（引导放行）
    assert c.get("/api/config").status_code == 200
    # 但数据/动作路由仍 401
    assert c.get("/api/runs").status_code == 401
    assert c.post("/api/runs", json={"email": "a@x.com"}).status_code == 401
    assert c.get("/api/accounts").status_code == 401
    (tmp_path / "runs" / "1").mkdir(parents=True)
    (tmp_path / "runs" / "1" / "log.txt").write_text("x", encoding="utf-8")
    assert c.get("/runs/1/log.txt").status_code == 401
    # 通过 PUT 设置密码（引导放行）
    assert c.put("/api/config", json={"panel_password": "newpw"}).status_code == 200
    # 设好密码后无 cookie 再访问配置 → 401
    assert c.get("/api/config").status_code == 401


def _authed(tmp_path, extra=None):
    """建库+登录，返回已带 cookie 的 TestClient（复用 test_app 的 _client）。"""
    from server.config_store import save_config

    save_config(tmp_path / "config.yaml", {"panel_password": "pw", **(extra or {})})
    c = _client(tmp_path)
    c.post("/api/login", json={"password": "pw"})
    return c


def test_xui_test_endpoint_ok(tmp_path, monkeypatch):
    import server.app as appmod

    class FakeXui:
        def __init__(self, base_url, username, password, **kw):
            self.base_url = base_url
        def list_inbounds(self):
            return [{"id": 1}, {"id": 2}]

    monkeypatch.setattr(appmod, "XuiClient", FakeXui)
    c = _authed(tmp_path)
    r = c.post("/api/xui/test", json={
        "base_url": "https://n.test:2053", "username": "u", "password": "p"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "inbound_count": 2}


def test_xui_test_endpoint_reports_failure(tmp_path, monkeypatch):
    import server.app as appmod
    from claude_register.xui import XuiError

    class FakeXui:
        def __init__(self, *a, **k): ...
        def list_inbounds(self):
            raise XuiError("bad creds")

    monkeypatch.setattr(appmod, "XuiClient", FakeXui)
    c = _authed(tmp_path)
    r = c.post("/api/xui/test", json={
        "base_url": "https://n.test:2053", "username": "u", "password": "x"})
    assert r.status_code == 400
    assert "bad creds" in r.json()["detail"]


def test_xui_cleanup_endpoint(tmp_path, monkeypatch):
    import server.app as appmod

    class FakePool:
        def __init__(self, *a, **k): ...
        def cleanup_expired(self):
            return {"n1": 3, "n2": 0}

    monkeypatch.setattr(appmod, "ProxyPool", FakePool)
    c = _authed(tmp_path, extra={
        "xui_enabled": True,
        "xui_nodes": [{"name": "n1", "base_url": "https://n1", "username": "u",
                       "password": "p", "proxy_host": ""}],
    })
    r = c.post("/api/xui/cleanup")
    assert r.status_code == 200
    assert r.json() == {"results": {"n1": 3, "n2": 0}, "total": 3}
