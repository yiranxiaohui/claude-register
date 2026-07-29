import time
import pytest
from server import db, runner
from server.config_store import Config


def _now():
    return "2026-07-29T00:00:00Z"


def test_start_runs_flow_and_records_success(tmp_path):
    conn = db.init_db(tmp_path / "t.db")
    r = runner.Runner(conn, tmp_path, _now)
    calls = {}

    def fake_flow(*, email=None, domain=None, config=None, **kw):
        from claude_register import console
        console.log("hi from flow")
        calls["email"] = email

    rid = r.start(Config(), email="a@x.com", flow_fn=fake_flow)
    # 等线程结束
    for _ in range(50):
        if db.get_run(conn, rid)["status"] != "running":
            break
        time.sleep(0.05)
    row = db.get_run(conn, rid)
    assert row["status"] == "success"
    log_txt = (tmp_path / "runs" / str(rid) / "log.txt").read_text()
    assert "hi from flow" in log_txt


def test_start_twice_is_busy(tmp_path):
    conn = db.init_db(tmp_path / "t.db")
    r = runner.Runner(conn, tmp_path, _now)

    def slow_flow(**kw):
        time.sleep(0.5)

    r.start(Config(), email="a@x.com", flow_fn=slow_flow)
    with pytest.raises(runner.RunnerBusy):
        r.start(Config(), email="b@x.com", flow_fn=slow_flow)


def test_flow_exception_marks_failed(tmp_path):
    conn = db.init_db(tmp_path / "t.db")
    r = runner.Runner(conn, tmp_path, _now)

    def boom(**kw):
        raise RuntimeError("kaboom")

    rid = r.start(Config(), email="a@x.com", flow_fn=boom)
    for _ in range(50):
        if db.get_run(conn, rid)["status"] != "running":
            break
        time.sleep(0.05)
    assert db.get_run(conn, rid)["status"] == "failed"
    assert "kaboom" in (tmp_path / "runs" / str(rid) / "log.txt").read_text()
