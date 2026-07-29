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
