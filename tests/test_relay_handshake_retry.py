"""握手超时的重试：上游瞬时挂住（撞并发限额的典型表现）应重试而非直接放弃。

背景：机场类上游超限时会把连接「挂住不回复」（见 test_relay_throttle 的实测
说明），表现为握手中途超时——而不是毫秒级 EOF。槽位随旧连接关闭很快周转，
换一条新连接重试大概率能过。一超时就放弃会把本可成活的请求白白报死，
线上表现就是刷屏「上游握手超时：timed out」。
"""

from __future__ import annotations

import socket
import struct
import threading

import pytest

from claude_register import socks_relay
from claude_register.socks_relay import SocksRelay


class FlakyHangUpstream:
    """免认证假上游：前 hang_first 条连接在握手第一步挂住不回复（模拟撞并发
    限额被上游挂起），之后的连接正常完成握手并回显收到的数据。"""

    def __init__(self, *, hang_first: int, echo: bytes = b"OK"):
        self.echo = echo
        self._hang_remaining = hang_first
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._hung: list[socket.socket] = []
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(16)
        self.port = self._sock.getsockname()[1]
        self.attempts = 0
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        with self._lock:
            self.attempts += 1
            hang = self._hang_remaining > 0
            if hang:
                self._hang_remaining -= 1
                # 留着不关：真实上游挂起时连接是开着的，只是不回复。
                self._hung.append(conn)
        if hang:
            return
        try:
            conn.settimeout(10)
            head = conn.recv(2)
            if len(head) < 2:
                return
            conn.recv(head[1])
            conn.sendall(b"\x05\x00")  # 免认证放行

            req = conn.recv(4)
            if req[3] == 0x03:
                conn.recv(conn.recv(1)[0])
            elif req[3] == 0x01:
                conn.recv(4)
            conn.recv(2)  # 端口
            conn.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
            payload = conn.recv(65535)
            conn.sendall(self.echo + payload)
        except Exception:
            pass
        finally:
            conn.close()

    def close(self) -> None:
        self._stop.set()
        for conn in self._hung:
            try:
                conn.close()
            except OSError:
                pass
        try:
            self._sock.close()
        except OSError:
            pass


@pytest.fixture(autouse=True)
def _fast_timings(monkeypatch):
    """挂住路径要吃满 HANDSHAKE_TIMEOUT，调小让测试跑得快。"""
    monkeypatch.setattr(socks_relay, "HANDSHAKE_TIMEOUT", 0.3)
    monkeypatch.setattr(socks_relay, "RETRY_BACKOFF", 0.05)
    monkeypatch.setattr(socks_relay, "RETRY_DEADLINE", 5.0)


def _connect_and_echo(relay_port: int, *, timeout: float = 8.0) -> bytes:
    s = socket.create_connection(("127.0.0.1", relay_port), timeout)
    s.settimeout(timeout)
    try:
        s.sendall(bytes([0x05, 0x01, 0x00]))
        assert s.recv(2) == bytes([0x05, 0x00]), "中继未免认证放行"
        d = b"example.com"
        s.sendall(b"\x05\x01\x00\x03" + bytes([len(d)]) + d + struct.pack("!H", 443))
        rep = s.recv(10)
        assert rep and rep[1] == 0x00, f"CONNECT 失败 rep={rep[1] if rep else 'EOF'}"
        s.sendall(b"A")
        return s.recv(100)
    finally:
        s.close()


def test_handshake_hang_then_recover_is_retried():
    """第一条连接握手挂住（超时），重试的新连接正常——请求应成活。"""
    up = FlakyHangUpstream(hang_first=1)
    try:
        with SocksRelay(f"socks5://127.0.0.1:{up.port}", max_upstream=3) as relay:
            assert _connect_and_echo(relay.port) == b"OKA"
        assert up.attempts >= 2, "握手超时后应换新连接重试"
    finally:
        up.close()


def test_persistent_hang_fails_within_deadline():
    """上游一直挂住时要在 RETRY_DEADLINE 量级内认输回失败码，
    不能靠重试把浏览器的导航超时预算耗光。"""
    import time

    up = FlakyHangUpstream(hang_first=100)
    try:
        with SocksRelay(f"socks5://127.0.0.1:{up.port}", max_upstream=3) as relay:
            start = time.monotonic()
            s = socket.create_connection(("127.0.0.1", relay.port), 8)
            s.settimeout(8)
            try:
                s.sendall(bytes([0x05, 0x01, 0x00]))
                s.recv(2)
                d = b"example.com"
                s.sendall(
                    b"\x05\x01\x00\x03" + bytes([len(d)]) + d + struct.pack("!H", 443)
                )
                rep = s.recv(10)
                assert rep and rep[1] != 0x00, "持续挂住应回明确失败码"
            finally:
                s.close()
            elapsed = time.monotonic() - start
            assert elapsed < socks_relay.RETRY_DEADLINE + socks_relay.HANDSHAKE_TIMEOUT + 1.0, (
                f"失败耗时 {elapsed:.1f}s，超出重试预算"
            )
    finally:
        up.close()
