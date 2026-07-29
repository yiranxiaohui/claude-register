"""本地 SOCKS5 中继测试。

背景：Firefox/Playwright 不支持 SOCKS5 用户名密码认证
（coreBundle.js `normalizeProxySettings` 直接抛 "Browser does not support
socks5 proxy authentication"）。中继在本地开一个免认证 SOCKS5 口，
把流量转给带凭据的上游，浏览器只连 127.0.0.1。

这些测试用真实 socket 跑完整握手，不 mock 协议——协议实现错了 mock 是发现不了的。
"""

from __future__ import annotations

import socket
import struct
import threading

import pytest

from claude_register.socks_relay import SocksRelay

NO_AUTH = 0x00
USER_PASS = 0x02


class FakeUpstream:
    """假的带认证 SOCKS5 上游，只接受指定凭据。

    记录收到的 CONNECT 目标，好断言中继确实把目标透传过去了。
    """

    def __init__(self, username: str, password: str, *, echo: bytes = b"HELLO"):
        self.username = username
        self.password = password
        self.echo = echo
        self.requested: list[tuple[str, int]] = []
        self.auth_attempts: list[tuple[str, str]] = []
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self.port = self._sock.getsockname()[1]
        self._stop = threading.Event()
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
        try:
            conn.settimeout(5)
            head = conn.recv(2)
            if len(head) < 2:
                return
            nmethods = head[1]
            methods = conn.recv(nmethods)
            if USER_PASS not in methods:
                conn.sendall(b"\x05\xff")  # 不接受任何方法
                return
            conn.sendall(bytes([0x05, USER_PASS]))

            conn.recv(1)  # 认证子协商版本
            ulen = conn.recv(1)[0]
            user = conn.recv(ulen).decode()
            plen = conn.recv(1)[0]
            pwd = conn.recv(plen).decode()
            self.auth_attempts.append((user, pwd))
            if user != self.username or pwd != self.password:
                conn.sendall(b"\x01\x01")  # 认证失败
                return
            conn.sendall(b"\x01\x00")

            req = conn.recv(4)
            atyp = req[3]
            if atyp == 0x03:
                dlen = conn.recv(1)[0]
                host = conn.recv(dlen).decode()
            elif atyp == 0x01:
                host = socket.inet_ntoa(conn.recv(4))
            else:
                conn.sendall(b"\x05\x08\x00\x01\x00\x00\x00\x00\x00\x00")
                return
            port = struct.unpack("!H", conn.recv(2))[0]
            self.requested.append((host, port))
            conn.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")

            # 建立"隧道"后回一段可识别的数据，证明字节确实双向流动
            payload = conn.recv(65535)
            conn.sendall(self.echo + payload)
        except Exception:
            pass
        finally:
            conn.close()

    def close(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass


@pytest.fixture
def upstream():
    up = FakeUpstream("alice", "s3cret")
    yield up
    up.close()


def socks5_noauth_connect(port: int, host: str, dport: int) -> socket.socket:
    """以免认证 SOCKS5 客户端身份连本地中继，返回已建好隧道的 socket。"""
    s = socket.create_connection(("127.0.0.1", port), 5)
    s.settimeout(5)
    s.sendall(bytes([0x05, 0x01, NO_AUTH]))
    reply = s.recv(2)
    assert reply == bytes([0x05, NO_AUTH]), f"中继应免认证放行，实际 {reply.hex()}"
    d = host.encode()
    s.sendall(b"\x05\x01\x00\x03" + bytes([len(d)]) + d + struct.pack("!H", dport))
    rep = s.recv(10)
    assert rep[1] == 0x00, f"CONNECT 应成功，rep={rep[1]}"
    return s


def test_relay_accepts_noauth_client_and_authenticates_upstream(upstream):
    """核心行为：浏览器侧免认证，上游侧自动带上凭据。"""
    with SocksRelay(
        f"socks5://alice:s3cret@127.0.0.1:{upstream.port}"
    ) as relay:
        s = socks5_noauth_connect(relay.port, "example.com", 443)
        s.sendall(b"PING")
        assert s.recv(100) == b"HELLOPING", "数据应双向透传"
        s.close()

    assert upstream.auth_attempts == [("alice", "s3cret")]
    assert upstream.requested == [("example.com", 443)]


def test_relay_forwards_target_hostname_not_resolved_ip(upstream):
    """域名要原样交给上游解析（socks5h 语义），不能本地解析成 IP——
    本地 DNS 可能被污染，也会泄露真实位置。"""
    with SocksRelay(f"socks5://alice:s3cret@127.0.0.1:{upstream.port}") as relay:
        s = socks5_noauth_connect(relay.port, "claude.ai", 443)
        s.close()

    assert upstream.requested == [("claude.ai", 443)]


def test_local_url_is_noauth_socks5(upstream):
    """给 Playwright 的地址必须是免认证 socks5://，否则又会踩到那条 authentication 报错。"""
    with SocksRelay(f"socks5://alice:s3cret@127.0.0.1:{upstream.port}") as relay:
        assert relay.local_url == f"socks5://127.0.0.1:{relay.port}"
        assert "alice" not in relay.local_url
        assert "s3cret" not in relay.local_url


def test_relay_binds_loopback_only(upstream):
    """免认证的口只能开在 127.0.0.1，绝不能对外——否则等于开放代理。"""
    with SocksRelay(f"socks5://alice:s3cret@127.0.0.1:{upstream.port}") as relay:
        assert relay.host == "127.0.0.1"


def test_bad_upstream_credentials_surface_as_connect_failure(upstream):
    """上游认证失败要让客户端拿到非 0 的 rep code，不能挂死等超时。"""
    with SocksRelay(f"socks5://alice:WRONG@127.0.0.1:{upstream.port}") as relay:
        s = socket.create_connection(("127.0.0.1", relay.port), 5)
        s.settimeout(5)
        s.sendall(bytes([0x05, 0x01, NO_AUTH]))
        assert s.recv(2) == bytes([0x05, NO_AUTH])
        d = b"example.com"
        s.sendall(b"\x05\x01\x00\x03" + bytes([len(d)]) + d + struct.pack("!H", 443))
        rep = s.recv(10)
        assert rep[1] != 0x00, "凭据错误时 CONNECT 必须失败，而不是假装成功"
        s.close()


def test_port_released_after_close(upstream):
    """退出后端口要真的释放，长期运行不能泄漏监听。"""
    with SocksRelay(f"socks5://alice:s3cret@127.0.0.1:{upstream.port}") as relay:
        port = relay.port
    probe = socket.socket()
    probe.settimeout(2)
    with pytest.raises(OSError):
        probe.connect(("127.0.0.1", port))
    probe.close()


class IpEchoUpstream(FakeUpstream):
    """假上游，对任何 CONNECT 都回一段固定的 HTTP 响应，body 是出口 IP。"""

    def __init__(self, ip: str = "203.0.113.9"):
        super().__init__("alice", "s3cret")
        self.ip = ip

    def _handle(self, conn):  # noqa: D102
        try:
            conn.settimeout(5)
            head = conn.recv(2)
            if len(head) < 2:
                return
            conn.recv(head[1])
            conn.sendall(bytes([0x05, USER_PASS]))
            conn.recv(1)
            ulen = conn.recv(1)[0]
            user = conn.recv(ulen).decode()
            plen = conn.recv(1)[0]
            pwd = conn.recv(plen).decode()
            self.auth_attempts.append((user, pwd))
            conn.sendall(b"\x01\x00")
            req = conn.recv(4)
            if req[3] == 0x03:
                host = conn.recv(conn.recv(1)[0]).decode()
            else:
                host = socket.inet_ntoa(conn.recv(4))
            port = struct.unpack("!H", conn.recv(2))[0]
            self.requested.append((host, port))
            conn.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
            conn.recv(65535)
            body = self.ip.encode()
            conn.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Length: "
                + str(len(body)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + body
            )
        except Exception:
            pass
        finally:
            conn.close()


def test_exit_ip_queried_through_relay(monkeypatch):
    """出口 IP 必须经中继查——本地 DNS 可能被 fake-ip 污染（Clash 等透明代理），
    本地直查会拿到 198.18.x.x 这种虚拟地址。

    这里用明文假上游验证 SOCKS 协议路径本身，TLS 包装被替换成直通，
    免去为跑测试签一套自签证书。
    """
    up = IpEchoUpstream("203.0.113.9")
    monkeypatch.setattr(SocksRelay, "_wrap_tls", staticmethod(lambda sock, host: sock))
    monkeypatch.setattr(SocksRelay, "_exit_ip_port", 80)
    try:
        with SocksRelay(f"socks5://alice:s3cret@127.0.0.1:{up.port}") as relay:
            assert relay.exit_ip() == "203.0.113.9"
        # 目标域名原样交给上游，没有被本地解析成 IP
        assert up.requested and not up.requested[0][0][0].isdigit()
    finally:
        up.close()


def test_exit_ip_returns_none_when_unavailable(upstream, monkeypatch):
    """查不到出口 IP 时返回 None，让调用方降级，而不是把整个启动搞崩。"""
    monkeypatch.setattr(SocksRelay, "_wrap_tls", staticmethod(lambda sock, host: sock))
    monkeypatch.setattr(SocksRelay, "_exit_ip_port", 80)
    with SocksRelay(f"socks5://alice:WRONG@127.0.0.1:{upstream.port}") as relay:
        assert relay.exit_ip() is None


def test_exit_ip_probe_failures_are_not_reported_as_errors(upstream, monkeypatch):
    """查出口 IP 时挨个试若干站点，前面的失败是正常降级，不该走 on_error——
    否则用户会在日志里看到一串吓人的『上游握手失败』，其实一切正常。"""
    reported = []
    monkeypatch.setattr(SocksRelay, "_wrap_tls", staticmethod(lambda sock, host: sock))
    monkeypatch.setattr(SocksRelay, "_exit_ip_port", 80)
    relay = SocksRelay(
        f"socks5://alice:WRONG@127.0.0.1:{upstream.port}",
        on_error=reported.append,
    )
    with relay:
        relay.exit_ip()
    assert reported == [], f"探测失败不该报错，实际报了：{reported}"


class FlakyUpstream(FakeUpstream):
    """前 N 条连接直接 EOF，之后正常。

    模拟实测到的上游并发上限：超出限额的连接在握手第一步就被关掉，
    但槽位很快释放，重试就能成功。
    """

    def __init__(self, fail_first: int):
        self.remaining_failures = fail_first
        self._lock = threading.Lock()
        super().__init__("alice", "s3cret")

    def _handle(self, conn):
        with self._lock:
            if self.remaining_failures > 0:
                self.remaining_failures -= 1
                conn.close()
                return
        super()._handle(conn)


def test_transient_upstream_rejection_is_retried():
    """上游有并发上限，超限的连接会被直接 EOF。这是暂时的——槽位很快释放，
    重试就能成功。一次失败就放弃会在注册中途白掉请求。"""
    up = FlakyUpstream(fail_first=2)
    try:
        with SocksRelay(f"socks5://alice:s3cret@127.0.0.1:{up.port}") as relay:
            s = socks5_noauth_connect(relay.port, "example.com", 443)
            s.sendall(b"PING")
            assert s.recv(100) == b"HELLOPING"
            s.close()
    finally:
        up.close()
    assert up.requested == [("example.com", 443)]


def test_retry_gives_up_and_reports_failure():
    """持续失败最终还是要如实报错，不能无限重试把浏览器吊死。"""
    up = FlakyUpstream(fail_first=10_000)
    reported = []
    try:
        relay = SocksRelay(
            f"socks5://alice:s3cret@127.0.0.1:{up.port}", on_error=reported.append
        )
        with relay:
            s = socket.create_connection(("127.0.0.1", relay.port), 5)
            s.settimeout(30)
            s.sendall(bytes([0x05, 0x01, NO_AUTH]))
            assert s.recv(2) == bytes([0x05, NO_AUTH])
            d = b"example.com"
            s.sendall(b"\x05\x01\x00\x03" + bytes([len(d)]) + d + struct.pack("!H", 443))
            rep = s.recv(10)
            assert rep[1] != 0x00, "重试用尽后必须回失败"
            s.close()
    finally:
        up.close()
    assert reported, "最终失败要报出来"
