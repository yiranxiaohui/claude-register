"""开放 API：鉴权、触发注册、查询结果（含长轮询/字段选择）、批量导出。"""
from __future__ import annotations

import csv
import io
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from server import db
from server.app import create_app
from server.config_store import load_config, save_config

KEY = "cr_test_key_0123456789"
H = {"Authorization": f"Bearer {KEY}"}


def _app(tmp_path, **cfg):
    base = {"panel_password": "pw", "api_enabled": True, "api_key": KEY,
            "anymail_api_key": "ak", "anymail_base_url": "https://m"}
    base.update(cfg)
    save_config(tmp_path / "config.yaml", base)
    return create_app(
        data_dir=tmp_path, config_path=tmp_path / "config.yaml",
        now_fn=lambda: "2026-09-24T00:00:00Z",
    )


@pytest.fixture
def fake_flow(monkeypatch):
    """替换注册流程：可控地阻塞/成功/失败，不起浏览器。"""
    import claude_register.flow as flow

    ctl = {"release": threading.Event(), "result": None, "error": None, "calls": []}
    ctl["release"].set()

    def run(**kw):
        ctl["calls"].append(kw)
        ctl["release"].wait(5)
        if ctl["error"]:
            raise RuntimeError(ctl["error"])
        return ctl["result"]

    monkeypatch.setattr(flow, "run", run)
    return ctl


def _ok_result(email="new@x.com", session_key="sk-ant-new"):
    return {"email": email, "sessionKey": session_key, "password": "pw-new",
            "proxy": "socks5://u:p@h:1", "mail_key": "mk", "mail_base_url": "https://mail",
            "display_name": ""}


def _seed(app):
    conn = app.state.cr.conn
    db.upsert_account(conn, "a@x.com", "x.com", "", "mb1", 1, "success",
                      password="pw-a", session_key="sk-a", proxy="socks5://p:1",
                      mail_key="mk-a", mail_base_url="https://mail")
    db.upsert_account(conn, "b@y.com", "y.com", "", "mb2", 2, "needs_manual",
                      password="pw-b")
    db.update_account_check(conn, "a@x.com", "alive", "2026-09-24T00:00:00Z")


# ---- 鉴权 ----


def test_api_disabled_by_default(tmp_path):
    save_config(tmp_path / "config.yaml", {"panel_password": "pw"})
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml")
    r = TestClient(app).get("/api/v1/fields", headers=H)
    assert r.status_code == 403


def test_api_enabled_without_key_is_403(tmp_path):
    c = TestClient(_app(tmp_path, api_key=None))
    # api_key=None 视作「不修改」→ 仍为空
    assert c.get("/api/v1/fields", headers=H).status_code == 403


def test_api_rejects_missing_or_wrong_key(tmp_path):
    c = TestClient(_app(tmp_path))
    assert c.get("/api/v1/fields").status_code == 401
    assert c.get("/api/v1/fields", headers={"Authorization": "Bearer nope"}).status_code == 401
    # key 放 query 不被接受（避免进访问日志）
    assert c.get(f"/api/v1/fields?api_key={KEY}").status_code == 401


def test_api_accepts_bearer_and_x_api_key(tmp_path):
    c = TestClient(_app(tmp_path))
    assert c.get("/api/v1/fields", headers=H).status_code == 200
    assert c.get("/api/v1/fields", headers={"X-API-Key": KEY}).status_code == 200


def test_panel_cookie_does_not_grant_open_api(tmp_path):
    c = TestClient(_app(tmp_path))
    c.post("/api/login", json={"password": "pw"})
    assert c.get("/api/v1/fields").status_code == 401


def test_api_key_does_not_grant_panel(tmp_path):
    c = TestClient(_app(tmp_path))
    assert c.get("/api/accounts", headers=H).status_code == 401


def test_rotate_api_key_invalidates_old(tmp_path):
    c = TestClient(_app(tmp_path))
    assert c.post("/api/api-key").status_code == 401  # 需面板登录
    c.post("/api/login", json={"password": "pw"})
    new = c.post("/api/api-key").json()["api_key"]
    assert new.startswith("cr_") and len(new) > 30 and new != KEY
    assert load_config(tmp_path / "config.yaml").api_key == new
    assert c.get("/api/v1/fields", headers=H).status_code == 401
    assert c.get("/api/v1/fields", headers={"X-API-Key": new}).status_code == 200


def test_saving_settings_with_blank_key_keeps_it(tmp_path):
    c = TestClient(_app(tmp_path))
    c.post("/api/login", json={"password": "pw"})
    c.put("/api/config", json={"api_key": "", "api_enabled": True})
    assert load_config(tmp_path / "config.yaml").api_key == KEY


# ---- 字段 / 代理 ----


def test_fields_describe(tmp_path):
    d = TestClient(_app(tmp_path)).get("/api/v1/fields", headers=H).json()
    keys = [f["key"] for f in d["fields"]]
    assert d["default_fields"] == ["email", "session_key", "proxy", "mail_base_url", "mail_key"]
    assert {"password", "check_status", "created_at"} <= set(keys)
    assert set(d["formats"]) == {"text", "json", "csv", "line"}


def test_proxies_hide_urls(tmp_path):
    app = _app(tmp_path, saved_proxies=[{"id": "p1", "name": "美国", "url": "socks5://u:secret@h:1"}])
    r = TestClient(app).get("/api/v1/proxies", headers=H).json()
    assert r == [{"id": "p1", "name": "美国"}]


# ---- 注册 ----


def test_register_success_returns_selected_fields(tmp_path, fake_flow):
    fake_flow["result"] = _ok_result()
    c = TestClient(_app(tmp_path))
    r = c.post("/api/v1/register", headers=H, json={})
    assert r.status_code == 202
    rid = r.json()["run_id"]
    res = c.get(f"/api/v1/register/{rid}?wait=5&fields=email,session_key,password", headers=H).json()
    assert res["status"] == "success"
    assert res["email"] == "new@x.com"
    assert res["account"] == {"email": "new@x.com", "session_key": "sk-ant-new", "password": "pw-new"}
    assert "export" not in res and "log_tail" not in res


def test_register_status_with_text_and_line_export(tmp_path, fake_flow):
    fake_flow["result"] = _ok_result()
    c = TestClient(_app(tmp_path))
    rid = c.post("/api/v1/register", headers=H).json()["run_id"]
    res = c.get(f"/api/v1/register/{rid}?wait=5&format=text", headers=H).json()
    assert res["export"] == (
        "email：new@x.com\nsessionkey：sk-ant-new\nproxy：socks5://u:p@h:1\n"
        "mailUrl：https://mail\nmailKey：mk\n"
    )
    res = c.get(f"/api/v1/register/{rid}?format=line&fields=email,session_key&sep=|", headers=H).json()
    assert res["export"] == "new@x.com|sk-ant-new\n"


def test_register_passes_email_domain_and_proxy(tmp_path, fake_flow):
    fake_flow["result"] = _ok_result("me@z.com")
    app = _app(tmp_path, saved_proxies=[{"id": "p1", "name": "美国", "url": "socks5://u:s@h:1080"}])
    c = TestClient(app)
    rid = c.post("/api/v1/register", headers=H,
                 json={"email": "me@z.com", "proxy_id": "p1"}).json()["run_id"]
    c.get(f"/api/v1/register/{rid}?wait=5", headers=H)
    call = fake_flow["calls"][0]
    assert call["email"] == "me@z.com"
    assert call["config"].register_proxy == "socks5://u:s@h:1080"


def test_register_rejects_bad_body_and_unknown_proxy(tmp_path, fake_flow):
    c = TestClient(_app(tmp_path))
    assert c.post("/api/v1/register", headers={**H, "content-type": "application/json"},
                  content=b"not json").status_code == 400
    assert c.post("/api/v1/register", headers=H, json=["x"]).status_code == 400
    assert c.post("/api/v1/register", headers=H, json={"email": 1}).status_code == 400
    assert c.post("/api/v1/register", headers=H, json={"proxy_id": "nope"}).status_code == 400
    assert fake_flow["calls"] == []


def test_register_busy_returns_active_run_id(tmp_path, fake_flow):
    fake_flow["release"].clear()
    fake_flow["result"] = _ok_result()
    c = TestClient(_app(tmp_path))
    rid = c.post("/api/v1/register", headers=H).json()["run_id"]
    try:
        r = c.post("/api/v1/register", headers=H)
        assert r.status_code == 409
        assert r.json()["active_run_id"] == rid
        # 未完成时查询立即返回 running
        assert c.get(f"/api/v1/register/{rid}", headers=H).json()["status"] == "running"
    finally:
        fake_flow["release"].set()
    assert c.get(f"/api/v1/register/{rid}?wait=5", headers=H).json()["status"] == "success"


def test_register_long_poll_waits_for_completion(tmp_path, fake_flow):
    fake_flow["release"].clear()
    fake_flow["result"] = _ok_result()
    c = TestClient(_app(tmp_path))
    rid = c.post("/api/v1/register", headers=H).json()["run_id"]
    threading.Timer(1.2, fake_flow["release"].set).start()
    t0 = time.time()
    res = c.get(f"/api/v1/register/{rid}?wait=10", headers=H).json()
    assert res["status"] == "success"
    assert 1.0 <= time.time() - t0 < 6


def test_register_long_poll_times_out_as_running(tmp_path, fake_flow):
    fake_flow["release"].clear()
    c = TestClient(_app(tmp_path))
    rid = c.post("/api/v1/register", headers=H).json()["run_id"]
    try:
        t0 = time.time()
        assert c.get(f"/api/v1/register/{rid}?wait=1", headers=H).json()["status"] == "running"
        assert time.time() - t0 >= 0.9
    finally:
        fake_flow["release"].set()
        c.get(f"/api/v1/register/{rid}?wait=5", headers=H)


def test_register_wait_capped(tmp_path):
    c = TestClient(_app(tmp_path))
    assert c.get("/api/v1/register/1?wait=999", headers=H).status_code == 422


def test_register_failed_includes_log_tail(tmp_path, fake_flow):
    fake_flow["error"] = "boom"
    c = TestClient(_app(tmp_path))
    rid = c.post("/api/v1/register", headers=H).json()["run_id"]
    res = c.get(f"/api/v1/register/{rid}?wait=5", headers=H).json()
    assert res["status"] == "failed"
    assert res["account"] is None
    assert any("boom" in line for line in res["log_tail"])


def test_register_without_session_key_is_needs_manual(tmp_path, fake_flow):
    fake_flow["result"] = _ok_result(session_key="")
    c = TestClient(_app(tmp_path))
    rid = c.post("/api/v1/register", headers=H).json()["run_id"]
    res = c.get(f"/api/v1/register/{rid}?wait=5", headers=H).json()
    assert res["status"] == "needs_manual"
    assert res["account"]["email"] == "new@x.com"
    assert "log_tail" in res


def test_register_status_unknown_run_and_bad_fields(tmp_path):
    c = TestClient(_app(tmp_path))
    assert c.get("/api/v1/register/999", headers=H).status_code == 404
    r = c.get("/api/v1/register/999?fields=email,nope", headers=H)
    assert r.status_code == 400 and "nope" in r.json()["detail"]


# ---- 批量导出 ----


def test_v1_export_json_default_fields(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    r = TestClient(app).get("/api/v1/accounts/export", headers=H)
    assert r.status_code == 200
    assert r.headers["x-total-count"] == "2"
    data = r.json()
    assert [d["email"] for d in data] == ["b@y.com", "a@x.com"]
    assert set(data[0]) == {"email", "session_key", "proxy", "mail_base_url", "mail_key"}


def test_v1_export_filters_and_fields(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    c = TestClient(app)
    r = c.get("/api/v1/accounts/export?status=success&fields=email,password,check_status", headers=H)
    assert r.json() == [{"email": "a@x.com", "password": "pw-a", "check_status": "alive"}]
    r = c.get("/api/v1/accounts/export?emails=B@Y.com,none@x.com&fields=email", headers=H)
    assert r.json() == [{"email": "b@y.com"}]
    r = c.get("/api/v1/accounts/export?check_status=dead", headers=H)
    assert r.json() == [] and r.headers["x-total-count"] == "0"


def test_v1_export_csv_and_line(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    c = TestClient(app)
    r = c.get("/api/v1/accounts/export?format=csv&fields=email,session_key,last_run_id", headers=H)
    assert r.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert rows == [["email", "session_key", "last_run_id"], ["b@y.com", "", "2"], ["a@x.com", "sk-a", "1"]]
    r = c.get("/api/v1/accounts/export?format=line&fields=email,password&status=success", headers=H)
    assert r.text == "a@x.com----pw-a\n"


def test_v1_export_rejects_bad_params(tmp_path):
    c = TestClient(_app(tmp_path))
    assert c.get("/api/v1/accounts/export?format=xml", headers=H).status_code == 400
    assert c.get("/api/v1/accounts/export?fields=,", headers=H).status_code == 400
    assert c.get("/api/v1/accounts/export?format=line&sep=", headers=H).status_code == 400


# ---- 面板导出（Cookie）----


def test_panel_export_default_is_unchanged_and_supports_options(tmp_path):
    app = _app(tmp_path)
    _seed(app)
    c = TestClient(app)
    c.post("/api/login", json={"password": "pw"})
    r = c.get("/api/accounts/export")
    assert r.headers["content-disposition"] == 'attachment; filename="accounts.txt"'
    assert r.text.startswith("email：b@y.com\nsessionkey：\nproxy：\nmailUrl：\nmailKey：\n\nemail：a@x.com")
    r = c.get("/api/accounts/export?format=json&fields=email,password&status=success")
    assert r.headers["content-disposition"] == 'attachment; filename="accounts.json"'
    assert json.loads(r.text) == [{"email": "a@x.com", "password": "pw-a"}]
    assert c.get("/api/export/fields").json()["default_fields"][0] == "email"


# ---- 逐个获取（claim）----


def _seed_claimable(app):
    conn = app.state.cr.conn
    for i, (email, check) in enumerate(
        [("c1@x.com", ""), ("c2@x.com", "dead"), ("c3@x.com", "alive"), ("c4@x.com", "")], 1
    ):
        db.upsert_account(conn, email, "x.com", "", f"mb{i}", i, "success",
                          password=f"pw{i}", session_key=f"sk-{i}")
        if check:
            db.update_account_check(conn, email, check, "2026-09-24T00:00:00Z")
    db.upsert_account(conn, "manual@x.com", "x.com", "", "mb9", 9, "needs_manual")


def test_v1_claim_one_at_a_time_and_marks_claimed(tmp_path):
    app = _app(tmp_path)
    _seed_claimable(app)
    c = TestClient(app)
    r = c.post("/api/v1/accounts/claim?fields=email,session_key", headers=H)
    assert r.status_code == 200
    assert r.json() == {
        "email": "c1@x.com", "claimed_at": "2026-09-24T00:00:00Z",
        "account": {"email": "c1@x.com", "session_key": "sk-1"}, "remaining": 2,
    }
    assert db.get_account(app.state.cr.conn, "c1@x.com")["claimed_at"] == "2026-09-24T00:00:00Z"
    # dead 的 c2、needs_manual 的账号都被跳过；不重复发放
    got = [c.post("/api/v1/accounts/claim", headers=H).json()["email"] for _ in range(2)]
    assert got == ["c3@x.com", "c4@x.com"]
    r = c.post("/api/v1/accounts/claim", headers=H)
    assert r.status_code == 404 and r.json()["detail"] == "没有可获取的账号"


def test_v1_claim_check_status_and_format(tmp_path):
    app = _app(tmp_path)
    _seed_claimable(app)
    c = TestClient(app)
    r = c.post("/api/v1/accounts/claim?check_status=alive&format=line&fields=email,password",
               headers=H)
    assert r.json()["email"] == "c3@x.com" and r.json()["export"] == "c3@x.com----pw3\n"
    assert r.json()["remaining"] == 0
    assert c.post("/api/v1/accounts/claim?check_status=alive", headers=H).status_code == 404
    assert c.post("/api/v1/accounts/claim?check_status=bogus", headers=H).status_code == 400
    assert c.post("/api/v1/accounts/claim?format=xml", headers=H).status_code == 400
    assert c.post("/api/v1/accounts/claim").status_code == 401


def test_v1_claim_concurrent_never_duplicates(tmp_path):
    app = _app(tmp_path)
    _seed_claimable(app)
    c = TestClient(app)
    results: list[int | str] = []

    def worker():
        r = c.post("/api/v1/accounts/claim", headers=H)
        results.append(r.json()["email"] if r.status_code == 200 else r.status_code)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    emails = [x for x in results if isinstance(x, str)]
    assert sorted(emails) == ["c1@x.com", "c3@x.com", "c4@x.com"]
    assert results.count(404) == 5


def test_claimed_export_filter_and_panel_toggle(tmp_path):
    app = _app(tmp_path)
    _seed_claimable(app)
    c = TestClient(app)
    c.post("/api/v1/accounts/claim", headers=H)
    r = c.get("/api/v1/accounts/export?claimed=true&fields=email,claimed_at", headers=H)
    assert r.json() == [{"email": "c1@x.com", "claimed_at": "2026-09-24T00:00:00Z"}]
    r = c.get("/api/v1/accounts/export?claimed=false&fields=email", headers=H)
    assert "c1@x.com" not in {x["email"] for x in r.json()}
    assert c.get("/api/v1/accounts/export?claimed=maybe", headers=H).status_code == 400

    c.post("/api/login", json={"password": "pw"})
    r = c.put("/api/accounts/c1@x.com/claimed", json={"claimed": False})
    assert r.status_code == 200 and r.json()["claimed_at"] == ""
    # 取消标记后可再次被获取
    assert c.post("/api/v1/accounts/claim", headers=H).json()["email"] == "c1@x.com"
    assert c.put("/api/accounts/c4@x.com/claimed", json={"claimed": True}).json()["claimed_at"]
    assert c.put("/api/accounts/c4@x.com/claimed", json={"claimed": "yes"}).status_code == 400
    assert c.put("/api/accounts/none@x.com/claimed", json={"claimed": True}).status_code == 404
    assert c.get("/api/accounts/export?claimed=true&format=line&fields=email").text == \
        "c4@x.com\nc1@x.com\n"


def test_claimed_at_survives_reregistration_upsert(tmp_path):
    app = _app(tmp_path)
    _seed_claimable(app)
    conn = app.state.cr.conn
    TestClient(app).post("/api/v1/accounts/claim", headers=H)
    db.upsert_account(conn, "c1@x.com", "x.com", "", "mb1", 10, "success", session_key="sk-new")
    assert db.get_account(conn, "c1@x.com")["claimed_at"] == "2026-09-24T00:00:00Z"
