"""真浏览器回归：浏览器的空闲长连接不能把中继槽位占满。

接管里用户依次打开多个站点时，Chromium 会把每个站点的 keep-alive 连接留在池里，
只在自己的每代理上限满了才关掉空闲连接；中继槽位按隧道生命周期持有，分不清空闲
与活动。浏览器上限高于中继槽位时，新站点只能排队到超时，页面报
ERR_SOCKS_CONNECTION_FAILED。这里用本地 HTTP 站点 + 假上游复现该场景。
"""
from __future__ import annotations

import socket
import socketserver
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from claude_register import socks_relay
from claude_register.browser import browser_proxy_pool_limits, launch_chromium
from claude_register.socks_relay import SocksRelay, _pipe, _recv_exactly

RELAY_SLOTS = 8
SITES = 14


class _Page(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive：浏览器会把连接留在池里

    def do_GET(self):
        body = b"<!doctype html><title>ok</title>ok"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class _Upstream(socketserver.ThreadingTCPServer):
    """无认证 SOCKS5 上游：任何域名都连到本地站点。"""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, target):
        self.target = target
        super().__init__(("127.0.0.1", 0), _UpstreamHandler)


class _UpstreamHandler(socketserver.BaseRequestHandler):
    def handle(self):
        client = self.request
        _recv_exactly(client, _recv_exactly(client, 2)[1])
        client.sendall(b"\x05\x00")
        _, _, _, atyp = _recv_exactly(client, 4)
        if atyp != socks_relay.ATYP_DOMAIN:
            return
        _recv_exactly(client, _recv_exactly(client, 1)[0])
        _recv_exactly(client, 2)
        target = socket.create_connection(self.server.target, 5)
        target.settimeout(None)
        client.sendall(b"\x05\x00\x00\x01" + b"\x00" * 6)
        back = threading.Thread(target=_pipe, args=(target, client), daemon=True)
        back.start()
        _pipe(client, target)
        back.join(5)
        target.close()


@pytest.fixture
def relay(monkeypatch):
    # 缩短排队上限：回归时几秒就能失败，而不是每个站点等 30s。
    monkeypatch.setattr(socks_relay, "SLOT_WAIT_TIMEOUT", 3.0)
    site = ThreadingHTTPServer(("127.0.0.1", 0), _Page)
    site.daemon_threads = True
    upstream = _Upstream(site.server_address)
    for server in (site, upstream):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    errors: list[str] = []
    relay = SocksRelay(
        f"socks5://127.0.0.1:{upstream.server_address[1]}",
        on_error=errors.append, max_upstream=RELAY_SLOTS,
    ).start()
    relay.errors = errors
    relay.site_port = site.server_address[1]
    yield relay
    relay.stop()
    for server in (upstream, site):
        server.shutdown()
        server.server_close()


def test_idle_browser_connections_do_not_starve_relay(relay):
    try:
        session = launch_chromium(
            headless=True, proxy={"server": relay.local_url},
            proxy_pool_limits=browser_proxy_pool_limits(RELAY_SLOTS),
        )
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Chromium 不可用：{exc}")
    try:
        page = session.context.pages[0]
        for i in range(SITES):
            # 等到 favicon 等请求都结束：否则导航会取消飞行中的请求、顺手关掉连接，
            # 保留不下空闲长连接，也就复现不了槽位被占满。
            response = page.goto(
                f"http://site{i}.test:{relay.site_port}/",
                wait_until="networkidle", timeout=10_000,
            )
            assert response is not None and response.ok
    finally:
        session.close()
    assert not [e for e in relay.errors if "槽位" in e], relay.errors
