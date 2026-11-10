"""Embedded Xray process control through Supervisor."""
from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from curl_cffi import requests


_STATUS_RE = re.compile(
    r"^xray\s+(?P<state>\S+)(?:\s+pid\s+(?P<pid>\d+),\s+uptime\s+(?P<uptime>.+))?",
    re.IGNORECASE,
)
_ACTIONS = frozenset(("start", "stop", "restart"))


class XrayError(RuntimeError):
    """A safe, user-facing Xray management error."""


class XrayManager:
    def __init__(
        self,
        config_path: Path,
        *,
        supervisor_config: str | None = None,
        binary: str = "/usr/local/bin/xray",
        supervisorctl: str | None = None,
    ):
        self.config_path = Path(config_path)
        # Keep the enable marker beside config.json as xray/enabled; unlike
        # with_suffix(), this preserves the filename expected by the entrypoint.
        self.enabled_path = self.config_path.parent / "enabled"
        self.supervisor_config = supervisor_config or os.environ.get(
            "CLAUDE_REGISTER_SUPERVISOR_CONFIG",
            "/etc/supervisor/conf.d/claude-register.conf",
        )
        self.binary = binary
        self.supervisorctl = supervisorctl or shutil.which("supervisorctl") or "supervisorctl"

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                [self.supervisorctl, "-c", self.supervisor_config, *args],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise XrayError("无法连接 Xray 进程管理器") from exc

    def _base_status(self) -> dict:
        return {
            "available": Path(self.binary).is_file(),
            "configured": self.config_path.is_file(),
            "state": "unknown",
            "pid": None,
            "uptime": None,
            "message": "",
        }

    def status(self) -> dict:
        result = self._base_status()
        if not result["available"]:
            result.update(state="unavailable", message="当前镜像未安装 Xray")
            return result
        if not result["configured"]:
            result.update(state="not_configured", message="尚未配置 Xray 出站")
            return result
        if not self.enabled_path.exists():
            result.update(state="stopped", message="Xray 已停止")
            return result
        try:
            proc = self._run("status", "xray")
        except XrayError as exc:
            result.update(state="unavailable", message=str(exc))
            return result
        line = (proc.stdout or proc.stderr or "").strip().splitlines()
        match = _STATUS_RE.match(line[0].strip()) if line else None
        if match:
            result["state"] = match.group("state").lower()
            result["pid"] = int(match.group("pid")) if match.group("pid") else None
            result["uptime"] = match.group("uptime") or None
        elif line and re.match(r"^xray\s+STOPPED\b", line[0], re.IGNORECASE):
            result["state"] = "stopped"
        else:
            result["state"] = "unknown"
        if proc.returncode != 0 and result["state"] == "unknown":
            result["message"] = "无法读取 Xray 状态"
        return result

    def _replace_config(self, contents: bytes | None) -> None:
        directory = self.config_path.parent
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.geteuid() == 0:
            os.chown(directory, 65532, 65532)
        os.chmod(directory, 0o700)
        if contents is None:
            self.config_path.unlink(missing_ok=True)
            return
        fd, temp_name = tempfile.mkstemp(prefix=".config-", dir=directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(contents)
            os.chmod(temp_name, 0o600)
            if os.geteuid() == 0:
                os.chown(temp_name, 65532, 65532)
            os.replace(temp_name, self.config_path)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass

    def snapshot_config(self) -> bytes | None:
        try:
            return self.config_path.read_bytes()
        except FileNotFoundError:
            return None

    def restore_config(self, contents: bytes | None) -> None:
        self._replace_config(contents)

    def configure_proxy(self, proxy_url: str) -> None:
        text = (proxy_url or "").strip()
        try:
            parts = urlsplit(text)
            port = parts.port
        except ValueError as exc:
            raise XrayError("SOCKS5 地址格式无效") from exc
        if (parts.scheme.lower() not in {"socks5", "socks5h"} or not parts.hostname
                or port is None or not 1 <= port <= 65535):
            raise XrayError("请输入有效的 socks5://host:port 地址")
        if parts.path not in ("", "/") or parts.query or parts.fragment:
            raise XrayError("SOCKS5 地址不能包含路径、查询参数或片段")

        server = {"address": parts.hostname, "port": port}
        username = unquote(parts.username or "")
        password = unquote(parts.password or "")
        if username or password:
            if not username or not password:
                raise XrayError("SOCKS5 用户名和密码必须同时填写")
            server["users"] = [{"user": username, "pass": password}]
        config = {
            "log": {"loglevel": "warning"},
            "inbounds": [{
                "listen": "127.0.0.1",
                "port": 10808,
                "protocol": "socks",
                "settings": {"auth": "noauth", "udp": False},
            }],
            "outbounds": [{
                "protocol": "socks",
                "settings": {"servers": [server]},
                "tag": "proxy",
            }],
            "routing": {
                "domainStrategy": "AsIs",
                "rules": [{
                    "type": "field",
                    "network": "tcp",
                    "outboundTag": "proxy",
                }],
            },
        }
        contents = (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        self._replace_config(contents)

    def upstream_proxy_url(self) -> str:
        try:
            config = json.loads(self.config_path.read_text(encoding="utf-8"))
            server = config["outbounds"][0]["settings"]["servers"][0]
            host = str(server["address"])
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            auth = ""
            users = server.get("users") or []
            if users:
                user = quote(str(users[0].get("user", "")), safe="")
                password = quote(str(users[0].get("pass", "")), safe="")
                auth = f"{user}:{password}@"
            return f"socks5h://{auth}{host}:{int(server['port'])}"
        except (OSError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise XrayError("无法读取已保存的 SOCKS5 上游") from exc

    def action(self, name: str) -> dict:
        if name not in _ACTIONS:
            raise XrayError("不支持的 Xray 操作")
        if not Path(self.binary).is_file():
            raise XrayError("当前镜像未安装 Xray")
        if name != "stop" and not self.config_path.is_file():
            raise XrayError("尚未配置 Xray 出站")
        if name in {"start", "restart"}:
            self.enabled_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if os.geteuid() == 0:
                os.chown(self.enabled_path.parent, 65532, 65532)
            self.enabled_path.touch(mode=0o600, exist_ok=True)
            os.chmod(self.enabled_path, 0o600)
            if os.geteuid() == 0:
                os.chown(self.enabled_path, 65532, 65532)
        command = name
        if name == "start":
            current = self._run("status", "xray")
            first_line = (current.stdout or current.stderr or "").strip().splitlines()
            if first_line and re.match(r"^xray\s+RUNNING\b", first_line[0], re.IGNORECASE):
                command = "restart"
        proc = self._run(command, "xray")
        if proc.returncode != 0:
            if name in {"start", "restart"}:
                self.enabled_path.unlink(missing_ok=True)
            raise XrayError(f"Xray 操作失败：{name}")
        if name == "stop":
            self.enabled_path.unlink(missing_ok=True)
        result = self.status()
        if name in {"start", "restart"} and result["state"] != "running":
            self.enabled_path.unlink(missing_ok=True)
            raise XrayError("Xray 未能正常启动，请检查上游配置")
        return result

    def test_egress(self, *, timeout: float = 15.0) -> dict:
        status = self.status()
        if status["state"] != "running":
            raise XrayError("Xray 当前未运行")
        try:
            response = requests.get(
                "https://api.ipify.org",
                proxy="socks5h://127.0.0.1:10808",
                timeout=timeout,
                impersonate="chrome",
            )
            response.raise_for_status()
            ip = response.text.strip()
            ipaddress.ip_address(ip)
        except Exception as exc:  # noqa: BLE001
            raise XrayError("Xray 出口测试失败") from exc
        return {"ok": True, "ip": ip}
