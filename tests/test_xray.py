from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.config_store import save_config
from server.xray import XrayError, XrayManager


class FakeSupervisor:
    def __init__(self, state="STOPPED"):
        self.state = state
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append(command[-2:])
        if command[-2] == "status":
            if self.state == "RUNNING":
                stdout = "xray RUNNING pid 123, uptime 0:01:02\n"
                return subprocess.CompletedProcess(command, 0, stdout, "")
            return subprocess.CompletedProcess(command, 3, "xray STOPPED Not started\n", "")
        if command[-2] == "start":
            self.state = "RUNNING"
            return subprocess.CompletedProcess(command, 0, "xray: started\n", "")
        if command[-2] == "stop":
            self.state = "STOPPED"
            return subprocess.CompletedProcess(command, 0, "xray: stopped\n", "")
        if command[-2] == "restart":
            self.state = "RUNNING"
            return subprocess.CompletedProcess(command, 0, "xray: restarted\n", "")
        return subprocess.CompletedProcess(command, 1, "", "bad command")


def test_xray_status_reports_running_without_secrets(tmp_path):
    config = tmp_path / "xray" / "config.json"
    config.parent.mkdir()
    config.write_text('{"password":"never-return-this"}')
    (config.parent / "enabled").touch()
    supervisor = FakeSupervisor("RUNNING")
    manager = XrayManager(config, binary="/bin/true", supervisorctl="supervisorctl")
    manager._run = lambda *args: supervisor(["supervisorctl", "-c", "conf", *args])

    assert manager.status() == {
        "available": True,
        "configured": True,
        "state": "running",
        "pid": 123,
        "uptime": "0:01:02",
        "message": "",
    }


def test_xray_status_reports_missing_config(tmp_path):
    manager = XrayManager(tmp_path / "missing.json", binary="/bin/true")
    assert manager.status()["state"] == "not_configured"


def test_xray_actions_require_installed_binary_and_config(tmp_path):
    manager = XrayManager(tmp_path / "missing.json", binary="/missing/xray")
    with pytest.raises(XrayError, match="未安装"):
        manager.action("start")
    with pytest.raises(XrayError, match="不支持"):
        manager.action("invalid")


def test_configure_proxy_writes_private_xray_config(tmp_path):
    config = tmp_path / "xray" / "config.json"
    manager = XrayManager(config, binary="/bin/true")

    manager.configure_proxy("socks5h://alice:s%40cret@proxy.example:1080")

    saved = json.loads(config.read_text())
    server = saved["outbounds"][0]["settings"]["servers"][0]
    assert server == {
        "address": "proxy.example",
        "port": 1080,
        "users": [{"user": "alice", "pass": "s@cret"}],
    }
    assert saved["inbounds"][0]["listen"] == "127.0.0.1"
    assert config.stat().st_mode & 0o777 == 0o600
    assert (config.parent / "enabled").exists() is False


@pytest.mark.parametrize("value", [
    "http://proxy.example:8080",
    "socks5://proxy.example",
    "socks5://proxy.example:70000",
    "socks5://user@proxy.example:1080",
    "socks5://proxy.example:1080/path",
])
def test_configure_proxy_rejects_invalid_urls(tmp_path, value):
    manager = XrayManager(tmp_path / "xray" / "config.json", binary="/bin/true")
    with pytest.raises(XrayError):
        manager.configure_proxy(value)


def _client(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml")
    client = TestClient(app)
    save_config(tmp_path / "config.yaml", {"panel_password": "pw"})
    client.post("/api/login", json={"password": "pw"})
    supervisor = FakeSupervisor()
    config_dir = tmp_path / "xray"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(json.dumps({
        "outbounds": [{"settings": {"servers": [{"address": "proxy.example", "port": 1080}]}}],
    }))
    app.state.cr.xray.binary = "/bin/true"
    app.state.cr.xray._run = lambda *args: supervisor(["supervisorctl", "-c", "conf", *args])
    app.state.cr.xray.test_egress = lambda: {"ok": True, "ip": "203.0.113.1"}
    return client, app, supervisor


def test_xray_management_requires_panel_auth(tmp_path):
    app = create_app(data_dir=tmp_path, config_path=tmp_path / "config.yaml")
    client = TestClient(app)
    assert client.get("/api/xray").status_code == 401
    assert client.post("/api/xray/start").status_code == 401


def test_xray_status_and_actions_are_authenticated(tmp_path, monkeypatch):
    client, app, supervisor = _client(tmp_path, monkeypatch)
    status = client.get("/api/xray")
    assert status.status_code == 200
    assert status.json()["state"] == "stopped"
    assert client.post("/api/xray/start").json()["state"] == "running"
    assert ["start", "xray"] in supervisor.calls
    assert client.post("/api/xray/stop").json()["state"] == "stopped"
    assert app.state.cr.config().register_proxy == "socks5h://proxy.example:1080"


def test_xray_configure_saves_loopback_proxy_and_starts(tmp_path, monkeypatch):
    client, app, _ = _client(tmp_path, monkeypatch)
    response = client.post("/api/xray/configure", json={
        "proxy": "socks5://user:secret@proxy.example:1080",
    })
    assert response.status_code == 200
    assert response.json()["active"] is True
    assert app.state.cr.config().register_proxy == "socks5h://127.0.0.1:10808"
    assert "secret" not in response.text
    config = json.loads((tmp_path / "xray" / "config.json").read_text())
    assert config["outbounds"][0]["settings"]["servers"][0]["users"][0]["pass"] == "secret"


def test_xray_configure_rejected_during_registration(tmp_path, monkeypatch):
    client, app, _ = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(app.state.cr.runner, "is_busy", lambda: True)
    response = client.post("/api/xray/configure", json={"proxy": "socks5://proxy.example:1080"})
    assert response.status_code == 409


def test_xray_egress_test_does_not_expose_exception_details(tmp_path, monkeypatch):
    client, app, _ = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(app.state.cr.xray, "test_egress", lambda: (_ for _ in ()).throw(XrayError("safe error")))
    response = client.post("/api/xray/test")
    assert response.status_code == 502
    assert response.json() == {"detail": "safe error"}
