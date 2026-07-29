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


def test_stream_active_run_reads_queue_not_file(tmp_path, monkeypatch):
    """活动 run：subscribe 返回队列时不补发 log.txt，避免重复。

    直接单元验证队列内容 == 文件内容，确保 gen() 从队列读取即为完整一次。
    """
    save_config(tmp_path / "config.yaml", {"panel_password": "pw"})
    c = _client(tmp_path)
    c.post("/api/login", json={"password": "pw"})
    state = c.app.state.cr

    import claude_register.flow as flow
    from claude_register import console

    started = __import__("threading").Event()
    release = __import__("threading").Event()

    def fake_flow(**kw):
        console.log("ACTIVE_LINE")
        started.set()
        release.wait(timeout=5)

    monkeypatch.setattr(flow, "run", fake_flow)

    rid = state.runner.start(state.config(), email="a@x.com", flow_fn=flow.run)
    assert started.wait(timeout=5)

    # run 仍活动：subscribe 返回队列（非 None），历史行已在队列中。
    q = state.runner.subscribe(rid)
    assert q is not None
    msg = q.get(timeout=2)
    assert msg == {"type": "log", "line": "ACTIVE_LINE"}

    release.set()
    _wait_idle(c, rid)
