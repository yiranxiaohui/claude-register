"""SOCKS relay cancellation, response preservation and total deadline regressions.

Only loopback sockets and dummy credentials are used. Timings are shortened
to expose production lifecycle/deadline behavior without external services.
"""
import socket
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from claude_register import socks_relay as sr


def recv_exact(sock, n):
    result = b""
    while len(result) < n:
        chunk = sock.recv(n - len(result))
        if not chunk:
            raise EOFError
        result += chunk
    return result


class Upstream:
    def __init__(self, handle):
        self.handle = handle
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(32)
        self.listener.settimeout(0.05)
        self.port = self.listener.getsockname()[1]
        self.stopped = threading.Event()
        self.sockets = []
        self.workers = []
        self.accepted = 0
        self.errors = []
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f"socks5://alice:dummy@127.0.0.1:{self.port}"

    def serve(self):
        while not self.stopped.is_set():
            try:
                conn, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            self.accepted += 1
            self.sockets.append(conn)
            worker = threading.Thread(target=self.run, args=(conn,), daemon=True)
            self.workers.append(worker)
            worker.start()

    def run(self, conn):
        try:
            conn.settimeout(3)
            self.handle(conn)
        except (OSError, EOFError):
            pass
        except Exception as exc:
            self.errors.append(exc)
        finally:
            conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.stopped.set()
        self.listener.close()
        self.thread.join(1)
        for conn in self.sockets:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()
        for worker in self.workers:
            worker.join(2)
        assert not self.errors, f"Fake upstream failed: {self.errors!r}"


def handshake(sock, *, pause=0.0, drip=0.0):
    head = recv_exact(sock, 2)
    recv_exact(sock, head[1])
    time.sleep(pause)
    sock.sendall(b"\x05\x02")
    head = recv_exact(sock, 2)
    recv_exact(sock, head[1])
    recv_exact(sock, recv_exact(sock, 1)[0])
    time.sleep(pause)
    sock.sendall(b"\x01\x00")
    head = recv_exact(sock, 4)
    assert head[3] == 3
    recv_exact(sock, recv_exact(sock, 1)[0])
    recv_exact(sock, 2)
    for byte in b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00":
        time.sleep(drip)
        sock.sendall(bytes([byte]))


def begin_connect(port):
    sock = socket.create_connection(("127.0.0.1", port), 3)
    sock.settimeout(8)
    sock.sendall(b"\x05\x01\x00")
    assert recv_exact(sock, 2) == b"\x05\x00"
    host = b"example.com"
    sock.sendall(b"\x05\x01\x00\x03" + bytes([len(host)]) + host + struct.pack("!H", 443))
    return sock


def connected(port):
    sock = begin_connect(port)
    assert recv_exact(sock, 10)[1] == 0
    return sock


def test_pending_response_survives_another_queued_connection():
    """A quiet, in-flight request must not be mistaken for idle keep-alive."""
    request_received = threading.Event()

    def respond_slowly(sock):
        handshake(sock)
        payload = sock.recv(64)
        request_received.set()
        # Exceed the former production idle cutoff (5s), with no traffic from
        # the upstream while the first request is legitimately in progress.
        if payload == b"first":
            time.sleep(5.5)
        sock.sendall(b"response:" + payload)

    with Upstream(respond_slowly) as up:
        with sr.SocksRelay(up.url, max_upstream=1) as relay:
            first = connected(relay.port)
            first.sendall(b"first")
            assert request_received.wait(1)

            def second_request():
                with connected(relay.port) as second:
                    second.sendall(b"second")
                    return second.recv(64)

            with ThreadPoolExecutor(max_workers=1) as executor:
                other = executor.submit(second_request)
                with first:
                    received = first.recv(64)
                other_received = other.result(timeout=8)
    assert received == b"response:first", (
        f"Pending response was truncated: first={received!r}, second={other_received!r}"
    )
    assert other_received == b"response:second"


def test_default_capacity_preserves_parallel_browser_connections(monkeypatch):
    """Several browser origins must connect without sacrificing earlier tunnels."""
    monkeypatch.delenv("RELAY_MAX_UPSTREAM", raising=False)
    monkeypatch.setattr(sr, "SLOT_WAIT_TIMEOUT", 0.5)

    def echo(sock):
        handshake(sock)
        sock.sendall(recv_exact(sock, 1))

    clients = []
    with Upstream(echo) as up:
        with sr.SocksRelay(up.url) as relay:
            try:
                for _ in range(8):
                    clients.append(connected(relay.port))
                for client in clients:
                    client.sendall(b"x")
                    assert recv_exact(client, 1) == b"x"
            finally:
                for client in clients:
                    client.close()


@pytest.mark.parametrize("cancel", ["stop_relay", "close_client"])
def test_cancellation_stops_upstream_handshake_retries(monkeypatch, cancel):
    """Cancelled work must close its handshake socket and stop making attempts."""
    monkeypatch.setattr(sr, "HANDSHAKE_TIMEOUT", 0.8)
    monkeypatch.setattr(sr, "RETRY_BACKOFF", 0.02)
    monkeypatch.setattr(sr, "RETRY_DEADLINE", 3.0)
    method_received = threading.Event()
    upstream_closed = threading.Event()

    def hang(sock):
        head = recv_exact(sock, 2)
        recv_exact(sock, head[1])
        method_received.set()
        sock.recv(1)  # Wait for the relay to cancel its upstream socket.
        upstream_closed.set()

    with Upstream(hang) as up:
        relay = sr.SocksRelay(up.url).start()
        client = begin_connect(relay.port)
        try:
            assert method_received.wait(1)
            if cancel == "stop_relay":
                relay.stop()
            else:
                client.close()
            attempts_at_cancel = up.accepted
            closed_promptly = upstream_closed.wait(0.4)
            time.sleep(1.1)
            attempts_after_cancel = up.accepted
        finally:
            client.close()
            relay.stop()
    assert attempts_after_cancel == attempts_at_cancel, (
        f"{cancel}: upstream attempts increased from {attempts_at_cancel} "
        f"to {attempts_after_cancel} after cancellation"
    )
    assert closed_promptly, "Cancelled handshake kept its upstream socket open"


@pytest.mark.parametrize("handshake_budget,total_budget,pause,drip", [
    (0.35, 0.5, 0.2, 0.1),  # Time across method negotiation and authentication.
    (0.35, 0.5, 0.0, 0.1),  # Partial CONNECT reply must not restart the clock.
    (2.0, 0.3, 0.0, 0.1),   # Even the first attempt respects a shorter total budget.
])
def test_handshake_has_a_total_deadline_not_a_per_recv_timeout(
    monkeypatch, handshake_budget, total_budget, pause, drip,
):
    """Slow partial replies must not extend the declared wall-clock budget."""
    monkeypatch.setattr(sr, "HANDSHAKE_TIMEOUT", handshake_budget)
    monkeypatch.setattr(sr, "RETRY_DEADLINE", total_budget)
    monkeypatch.setattr(sr, "CONNECT_RETRIES", 1)

    def slow_reply(sock):
        handshake(sock, pause=pause, drip=drip)

    result = None
    error = None
    with Upstream(slow_reply) as up:
        started = time.monotonic()
        try:
            result = sr._connect_upstream(sr.SocksRelay._parse_upstream(up.url), "example.com", 443)
        except sr.UpstreamError as exc:
            error = exc
        finally:
            elapsed = time.monotonic() - started
            if result is not None:
                result.close()
    assert error is not None and elapsed < min(handshake_budget, total_budget) + 0.3, (
        f"Total budget={total_budget}s, elapsed={elapsed:.2f}s, "
        f"connection_succeeded={result is not None}, error={error!r}"
    )


def test_cancelled_queued_client_never_connects_upstream(monkeypatch):
    queued = threading.Event()

    def hold(sock):
        handshake(sock)
        sock.recv(1)

    with Upstream(hold) as up:
        with sr.SocksRelay(up.url, max_upstream=1) as relay:
            first = connected(relay.port)
            acquire = relay._server.acquire_slot

            def waiting(*args):
                queued.set()
                return acquire(*args)

            monkeypatch.setattr(relay._server, "acquire_slot", waiting)
            abandoned = begin_connect(relay.port)
            try:
                assert queued.wait(1)
                abandoned.close()
                # Release the first slot only after the queued client has left.
                first.close()
                time.sleep(0.3)
                assert up.accepted == 1
            finally:
                first.close()
                abandoned.close()


@pytest.mark.parametrize("cancel", ["deadline", "stop_relay", "close_client"])
def test_slow_dns_is_bounded_and_never_connects_after_cancellation(monkeypatch, cancel):
    resolving = threading.Event()
    release_dns = threading.Event()
    resolved = threading.Event()
    getaddrinfo = socket.getaddrinfo

    def slow_dns(host, port, *args, **kwargs):
        if host == "slow.test":
            resolving.set()
            release_dns.wait(3)
            host = "127.0.0.1"
        result = getaddrinfo(host, port, *args, **kwargs)
        if release_dns.is_set():
            resolved.set()
        return result

    monkeypatch.setattr(socket, "getaddrinfo", slow_dns)
    monkeypatch.setattr(sr, "RETRY_DEADLINE", 0.3 if cancel == "deadline" else 3)
    errors = []
    with Upstream(lambda sock: handshake(sock)) as up:
        relay = sr.SocksRelay(up.url.replace("127.0.0.1", "slow.test"), on_error=errors.append).start()
        client = begin_connect(relay.port)
        try:
            assert resolving.wait(1)
            started = time.monotonic()
            if cancel == "deadline":
                assert recv_exact(client, 10)[1] != 0
                assert time.monotonic() - started < 0.6
                assert errors and "解析上游地址" in errors[0]
            elif cancel == "stop_relay":
                relay.stop()
            else:
                client.close()
            time.sleep(0.15)
            release_dns.set()
            assert resolved.wait(1)
            time.sleep(0.15)
            assert up.accepted == 0, "Late DNS response opened an abandoned connection"
            if cancel != "deadline":
                assert errors == []
        finally:
            release_dns.set()
            client.close()
            relay.stop()


def test_client_half_close_still_receives_complete_response():
    """Once connected, TCP half-close must continue to drain the response."""
    payload = b"request" * 100
    response = b"response" * 10000

    def reply_after_eof(sock):
        handshake(sock)
        received = b""
        while chunk := sock.recv(4096):
            received += chunk
        assert received == payload
        sock.sendall(response)

    with Upstream(reply_after_eof) as up:
        with sr.SocksRelay(up.url) as relay:
            with connected(relay.port) as client:
                client.sendall(payload)
                client.shutdown(socket.SHUT_WR)
                assert recv_exact(client, len(response)) == response
