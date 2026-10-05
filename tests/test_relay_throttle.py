"""上游并发限流：中继对上游的并发连接数必须闸到 max_upstream。

背景：机场类上游有硬并发上限（实测这个上游约 4 条），超限的 CONNECT 会被
挂住（不回复）而不是快速 EOF。中继若无限并发转发，claude.ai 登录页十几条
并发就会把关键连接（hcaptcha、验证码提交）挤掉，表现为满屏「上游握手超时」
且注册偶发失败。限流后超出的连接在本地排队等槽位，全部得以通过。
"""

from __future__ import annotations

import socket
import struct
import threading
import time

import pytest

from claude_register import socks_relay
from claude_register.socks_relay import SocksRelay

USER_PASS = 0x02


class CappedUpstream:
    """带认证的假上游，模拟硬并发上限。

    同时最多 cap 条隧道在跑；超限的 CONNECT 在回复前被挂住，直到某条隧道
    结束腾出槽位——这正是真实机场上游超限时的行为（hang，而非 EOF）。
    记录同时在跑的隧道峰值，供断言「中继没有超订上游」。
    """

    def __init__(
        self,
        username: str,
        password: str,
        *,
        cap: int,
        hold: float = 0.0,
        echo: bytes = b"OK",
    ):
        self.username = username
        self.password = password
        self.echo = echo
        self._hold = hold
        self._slot = threading.Semaphore(cap)
        self._active = 0
        self._max_active = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(64)
        self.port = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    @property
    def max_active(self) -> int:
        with self._lock:
            return self._max_active

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        acquired = False
        try:
            conn.settimeout(10)
            head = conn.recv(2)
            if len(head) < 2:
                return
            methods = conn.recv(head[1])
            if USER_PASS not in methods:
                conn.sendall(b"\x05\xff")
                return
            conn.sendall(bytes([0x05, USER_PASS]))

            conn.recv(1)  # 认证子协商版本
            ulen = conn.recv(1)[0]
            user = conn.recv(ulen).decode()
            plen = conn.recv(1)[0]
            pwd = conn.recv(plen).decode()
            if user != self.username or pwd != self.password:
                conn.sendall(b"\x01\x01")
                return
            conn.sendall(b"\x01\x00")

            req = conn.recv(4)
            atyp = req[3]
            if atyp == 0x03:
                conn.recv(conn.recv(1)[0])
            elif atyp == 0x01:
                conn.recv(4)
            else:
                conn.sendall(b"\x05\x08\x00\x01\x00\x00\x00\x00\x00\x00")
                return
            conn.recv(2)  # 端口

            # 硬并发上限：拿不到槽位就挂住（不回 CONNECT 成功），直到有隧道结束。
            while not self._slot.acquire(timeout=0.05):
                if self._stop.is_set():
                    return
            acquired = True
            with self._lock:
                self._active += 1
                self._max_active = max(self._max_active, self._active)

            conn.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
            payload = conn.recv(65535)
            # 停一会儿再回，制造隧道重叠窗口，好让并发峰值测量有意义。
            if self._hold:
                time.sleep(self._hold)
            conn.sendall(self.echo + payload)
        except Exception:
            pass
        finally:
            if acquired:
                with self._lock:
                    self._active -= 1
                self._slot.release()
            conn.close()

    def close(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass


@pytest.fixture(autouse=True)
def _fast_handshake(monkeypatch):
    """超限连接若冲到上游会撞 HANDSHAKE_TIMEOUT，调小让失败路径也跑得快。"""
    monkeypatch.setattr(socks_relay, "HANDSHAKE_TIMEOUT", 1.0)


def _connect_and_echo(
    relay_port: int, host: str, dport: int, *, payload: bytes, timeout: float = 8.0
) -> bytes:
    """免认证连本地中继，建隧道、发一段、收回显，返回收到的字节。"""
    s = socket.create_connection(("127.0.0.1", relay_port), timeout)
    s.settimeout(timeout)
    try:
        s.sendall(bytes([0x05, 0x01, 0x00]))
        if s.recv(2) != bytes([0x05, 0x00]):
            raise AssertionError("中继未免认证放行")
        d = host.encode()
        s.sendall(b"\x05\x01\x00\x03" + bytes([len(d)]) + d + struct.pack("!H", dport))
        rep = s.recv(10)
        if not rep or rep[1] != 0x00:
            raise AssertionError(f"CONNECT 失败 rep={rep[1] if rep else 'EOF'}")
        s.sendall(payload)
        return s.recv(100)
    finally:
        s.close()


def test_relay_caps_concurrent_upstream_and_all_succeed():
    """max_upstream=2 对 cap=2 的上游：6 条并发全部成功，
    且上游同时在跑的隧道峰值不超过 2（本地排队，不超订上游）。"""
    up = CappedUpstream("alice", "s3cret", cap=2)
    try:
        with SocksRelay(
            f"socks5://alice:s3cret@127.0.0.1:{up.port}", max_upstream=2
        ) as relay:
            results: dict[int, bytes] = {}
            errors: dict[int, Exception] = {}

            def worker(i: int) -> None:
                try:
                    results[i] = _connect_and_echo(
                        relay.port, "example.com", 443, payload=bytes([65 + i])
                    )
                except Exception as exc:  # noqa: BLE001
                    errors[i] = exc

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=25)

        assert not errors, f"所有连接都应排队通过，实际有失败：{errors}"
        assert len(results) == 6
        assert all(v == b"OK" + bytes([65 + i]) for i, v in results.items())
        assert up.max_active <= 2, f"中继不应超订上游，峰值并发 {up.max_active} > 2"
    finally:
        up.close()


def test_relay_peak_upstream_concurrency_is_bounded_by_max_upstream():
    """上游 cap 放宽到不设限（16），由中继的 max_upstream=3 独立决定峰值：
    8 条并发下，上游同时在跑的隧道峰值必须恰好被闸在 3。

    这条和上面那条互补——上面证「超订会失败」，这条直接量峰值,确保限流生效
    而非靠假上游自己的 cap 兜底。"""
    up = CappedUpstream("alice", "s3cret", cap=16, hold=0.15)
    try:
        with SocksRelay(
            f"socks5://alice:s3cret@127.0.0.1:{up.port}", max_upstream=3
        ) as relay:
            errors: dict[int, Exception] = {}

            def worker(i: int) -> None:
                try:
                    _connect_and_echo(
                        relay.port, "example.com", 443, payload=bytes([65 + i])
                    )
                except Exception as exc:  # noqa: BLE001
                    errors[i] = exc

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=25)

        assert not errors, f"排队通过不应失败：{errors}"
        assert up.max_active == 3, f"中继应把上游并发闸在 3，实测峰值 {up.max_active}"
    finally:
        up.close()


def test_slot_wait_timeout_replies_failure_not_hang(monkeypatch):
    """槽位被长期占满时，等待超过 SLOT_WAIT_TIMEOUT 的连接应收到明确的
    SOCKS 失败回复，而不是无限挂住。"""
    monkeypatch.setattr(socks_relay, "SLOT_WAIT_TIMEOUT", 0.5)
    up = CappedUpstream("alice", "s3cret", cap=1)
    try:
        with SocksRelay(
            f"socks5://alice:s3cret@127.0.0.1:{up.port}", max_upstream=1
        ) as relay:
            d = b"example.com"
            # 占住唯一槽位：建好隧道后既不发数据也不关闭，让它一直挂着。
            hog = socket.create_connection(("127.0.0.1", relay.port), 8)
            hog.settimeout(8)
            hog.sendall(bytes([0x05, 0x01, 0x00]))
            hog.recv(2)
            hog.sendall(b"\x05\x01\x00\x03" + bytes([len(d)]) + d + struct.pack("!H", 443))
            assert hog.recv(10)[1] == 0x00, "占位连接应先建好隧道"

            # 第二条连接：拿不到槽位，等 0.5s 后应收到失败回复而非挂死。
            s = socket.create_connection(("127.0.0.1", relay.port), 8)
            s.settimeout(8)
            try:
                s.sendall(bytes([0x05, 0x01, 0x00]))
                s.recv(2)
                s.sendall(
                    b"\x05\x01\x00\x03" + bytes([len(d)]) + d + struct.pack("!H", 443)
                )
                rep = s.recv(10)
                assert rep and rep[1] != 0x00, "槽位等待超时应回失败码，而非挂死或成功"
            finally:
                s.close()
                hog.close()
    finally:
        up.close()


def _open_tunnel(relay_port: int, host: str = "example.com") -> tuple[socket.socket, int]:
    """建隧道但不发数据，返回 (socket, rep)。"""
    s = socket.create_connection(("127.0.0.1", relay_port), 8)
    s.settimeout(8)
    s.sendall(bytes([0x05, 0x01, 0x00]))
    s.recv(2)
    d = host.encode()
    s.sendall(b"\x05\x01\x00\x03" + bytes([len(d)]) + d + struct.pack("!H", 443))
    rep = s.recv(10)
    return s, (rep[1] if rep else -1)


def test_idle_tunnel_is_evicted_when_someone_waits(monkeypatch):
    """槽位被一条闲置的 keep-alive 隧道占着时，排队的新连接应把它挤掉并顺利通过，
    而不是等到 SLOT_WAIT_TIMEOUT 失败——Chromium 闲置连接一留就是几分钟。"""
    monkeypatch.setattr(socks_relay, "SLOT_WAIT_TIMEOUT", 5.0)
    up = CappedUpstream("alice", "s3cret", cap=1)
    try:
        with SocksRelay(
            f"socks5://alice:s3cret@127.0.0.1:{up.port}", max_upstream=1,
            idle_evict_after=0.3,
        ) as relay:
            hog, rep = _open_tunnel(relay.port)
            assert rep == 0x00
            try:
                started = time.monotonic()
                got = _connect_and_echo(relay.port, "example.com", 443, payload=b"X")
                assert got == b"OKX"
                assert time.monotonic() - started < 3.0, "应在回收闲置隧道后很快拿到槽位"
                assert hog.recv(10) == b"", "被回收的闲置隧道应被掐断"
            finally:
                hog.close()
        assert up.max_active <= 1, "回收后仍不能超订上游"
    finally:
        up.close()


def test_idle_eviction_can_be_disabled(monkeypatch):
    monkeypatch.setattr(socks_relay, "SLOT_WAIT_TIMEOUT", 0.6)
    up = CappedUpstream("alice", "s3cret", cap=1)
    try:
        with SocksRelay(
            f"socks5://alice:s3cret@127.0.0.1:{up.port}", max_upstream=1,
            idle_evict_after=None,
        ) as relay:
            hog, rep = _open_tunnel(relay.port)
            try:
                _, rep2 = _open_tunnel(relay.port)
                assert rep2 != 0x00, "关掉回收时应照旧排队到超时失败"
            finally:
                hog.close()
    finally:
        up.close()


def test_evict_idle_picks_longest_idle_over_threshold():
    server = socks_relay._Server(("127.0.0.1", 0), socks_relay._Handler,
                                 max_upstream=1, idle_evict_after=1.0)
    try:
        now = time.monotonic()

        class T:
            def __init__(self, idle):
                self.last_active = now - idle
                self.evicted = False
                self.killed = False

            def evict(self):
                self.killed = True

        busy, old, older = T(0.2), T(2.0), T(5.0)
        for t in (busy, old, older):
            server.add_tunnel(t)
        assert server.evict_idle() is True
        assert older.killed and not old.killed and not busy.killed
        assert server.evict_idle() is True
        assert old.killed and not busy.killed
        assert server.evict_idle() is False, "活跃隧道不能被回收"
    finally:
        server.server_close()


@pytest.mark.parametrize("host", ["mtalk.google.com", "browser-intake-us5-datadoghq.com"])
def test_denied_hosts_rejected_without_touching_upstream(host):
    """拒绝名单里的目标当场回 not-allowed，不连上游、不占槽位。"""
    up = CappedUpstream("alice", "s3cret", cap=1)
    try:
        with SocksRelay(
            f"socks5://alice:s3cret@127.0.0.1:{up.port}", max_upstream=1,
            deny_hosts=("mtalk.google.com", "browser-intake-us5-datadoghq.com"),
        ) as relay:
            hog, rep = _open_tunnel(relay.port)  # 占满唯一槽位
            try:
                started = time.monotonic()
                s, rep = _open_tunnel(relay.port, host)
                s.close()
                assert rep == 0x02
                assert time.monotonic() - started < 1.0, "拒绝名单不该排队等槽位"
            finally:
                hog.close()
        assert up.max_active == 1, "被拒的目标不应连到上游"
    finally:
        up.close()


def test_deny_match_is_suffix_based_not_substring():
    assert socks_relay._host_matches("a.datadoghq.com", ("datadoghq.com",))
    assert socks_relay._host_matches("DATADOGHQ.com.", ("datadoghq.com",))
    assert not socks_relay._host_matches("notdatadoghq.com", ("datadoghq.com",))
    assert not socks_relay._host_matches("claude.ai", ("datadoghq.com",))
