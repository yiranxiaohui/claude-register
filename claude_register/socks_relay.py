"""本地免认证 SOCKS5 中继。

为什么需要它：Firefox / Playwright 明确不支持 SOCKS5 用户名密码认证——
playwright driver 的 `normalizeProxySettings` 里就一句

    if (url.protocol === "socks5:" && (proxy.username || proxy.password))
      throw new Error(`Browser does not support socks5 proxy authentication`);

而机场类代理基本都是带凭据的。于是在本地开一个免认证 SOCKS5 口，浏览器连
127.0.0.1，中继替它跟上游完成认证。Playwright 内部对付同类问题用的也是这招
（coreBundle.js 里的 `socks5://127.0.0.1:${this._socksProxy.port()}`）。

只实现 CONNECT + 域名/IPv4 地址类型——浏览器代理场景用不到 BIND / UDP ASSOCIATE。
"""

from __future__ import annotations

import socket
import socketserver
import struct
import threading
import time
from urllib.parse import unquote, urlsplit

SOCKS_VERSION = 0x05
AUTH_NONE = 0x00
AUTH_USER_PASS = 0x02
AUTH_UNACCEPTABLE = 0xFF
CMD_CONNECT = 0x01
ATYP_IPV4 = 0x01
ATYP_DOMAIN = 0x03
ATYP_IPV6 = 0x04

REP_SUCCESS = 0x00
REP_GENERAL_FAILURE = 0x01
REP_HOST_UNREACHABLE = 0x04
REP_CMD_NOT_SUPPORTED = 0x07
REP_ATYP_NOT_SUPPORTED = 0x08

# 上游握手（连接 + 认证 + CONNECT）的超时。握手阶段卡住说明上游有问题，
# 早点失败好过让浏览器空等到它自己的导航超时。
HANDSHAKE_TIMEOUT = 20.0
PIPE_BUFFER = 65536

# 查出口 IP 用的站点。走中继查（域名交给上游解析），因为本地 DNS 可能被
# fake-ip 污染——Clash 一类透明代理会把域名解析成 198.18.x.x，本地直查得到的
# 是虚拟地址，拿去 CONNECT 上游只会被拒。
EXIT_IP_HOSTS = ("api.ipify.org", "checkip.amazonaws.com", "icanhazip.com")
EXIT_IP_TIMEOUT = 12.0

# 机场类代理常有并发连接上限（实测这个上游约 4 条），超限的连接在握手第一步就被
# EOF 掉。但槽位随着旧连接关闭很快释放，短暂重试就能过——一次失败就放弃会在
# 注册流程中途白掉请求。只对「握手阶段就被拒」重试，CONNECT 已经被上游明确
# 拒绝（rep != 0）说明是目标本身的问题，重试没有意义。
CONNECT_RETRIES = 4
RETRY_BACKOFF = 0.4


def _looks_like_ipv4(text: str) -> bool:
    try:
        socket.inet_aton(text)
    except OSError:
        return False
    return text.count(".") == 3


def _recv_exactly(sock: socket.socket, n: int) -> bytes:
    """收满 n 字节。收不满说明对端提前关了，抛 ConnectionError。

    SOCKS 是长度前缀协议，短读会让后续解析全部错位——必须收满。
    """
    chunks: list[bytes] = []
    remaining = n
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionError(f"对端提前关闭，还差 {remaining} 字节")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


class UpstreamError(Exception):
    """连接/认证上游失败。携带该回给客户端的 SOCKS rep code。"""

    def __init__(self, message: str, rep: int = REP_GENERAL_FAILURE, *, retryable: bool = False):
        super().__init__(message)
        self.rep = rep
        # 握手阶段被拒（EOF / 连不上）多半是撞上了并发限额，重试有意义；
        # 上游明确回了 rep != 0 是目标本身的问题，重试只是浪费时间。
        self.retryable = retryable


def _connect_upstream_once(cfg: dict, host: str, port: int) -> socket.socket:
    """跟上游完成 SOCKS5 握手，返回已建好隧道的 socket。

    host 原样透传给上游解析（socks5h 语义）：本地 DNS 可能被污染，
    而且本地解析会泄露我们真实的地理位置。
    """
    try:
        up = socket.create_connection((cfg["host"], cfg["port"]), HANDSHAKE_TIMEOUT)
    except OSError as exc:
        raise UpstreamError(
            f"连不上上游代理：{exc}", REP_HOST_UNREACHABLE, retryable=True
        ) from exc

    up.settimeout(HANDSHAKE_TIMEOUT)
    try:
        want_auth = bool(cfg.get("username") or cfg.get("password"))
        methods = [AUTH_NONE, AUTH_USER_PASS] if want_auth else [AUTH_NONE]
        up.sendall(bytes([SOCKS_VERSION, len(methods)]) + bytes(methods))
        ver, method = _recv_exactly(up, 2)
        if ver != SOCKS_VERSION:
            raise UpstreamError(f"上游不是 SOCKS5（版本 {ver}）")
        if method == AUTH_UNACCEPTABLE:
            raise UpstreamError("上游拒绝了所有认证方式")
        if method == AUTH_USER_PASS:
            user = cfg.get("username", "").encode()
            pwd = cfg.get("password", "").encode()
            up.sendall(b"\x01" + bytes([len(user)]) + user + bytes([len(pwd)]) + pwd)
            _, status = _recv_exactly(up, 2)
            if status != 0x00:
                raise UpstreamError("上游认证失败：用户名或密码不对")
        elif method != AUTH_NONE:
            raise UpstreamError(f"上游要求不支持的认证方式 {method}")

        d = host.encode()
        up.sendall(
            bytes([SOCKS_VERSION, CMD_CONNECT, 0x00, ATYP_DOMAIN, len(d)])
            + d
            + struct.pack("!H", port)
        )
        _, rep, _, atyp = _recv_exactly(up, 4)
        if atyp == ATYP_IPV4:
            _recv_exactly(up, 4)
        elif atyp == ATYP_DOMAIN:
            _recv_exactly(up, _recv_exactly(up, 1)[0])
        elif atyp == ATYP_IPV6:
            _recv_exactly(up, 16)
        _recv_exactly(up, 2)  # 绑定端口
        if rep != REP_SUCCESS:
            raise UpstreamError(f"上游拒绝 CONNECT {host}:{port}（rep={rep}）", rep)
    except UpstreamError:
        up.close()
        raise
    except (OSError, ConnectionError) as exc:
        up.close()
        # 握手途中被 EOF / 超时——典型的撞并发限额表现，值得重试。
        raise UpstreamError(f"上游握手失败：{exc}", retryable=True) from exc

    up.settimeout(None)
    return up


def _connect_upstream(cfg: dict, host: str, port: int) -> socket.socket:
    """带重试的上游连接。只重试握手阶段的失败（多半是并发限额），
    上游明确拒绝的目标不重试。"""
    last: UpstreamError | None = None
    for attempt in range(CONNECT_RETRIES):
        try:
            return _connect_upstream_once(cfg, host, port)
        except UpstreamError as exc:
            last = exc
            if not exc.retryable:
                raise
            if attempt < CONNECT_RETRIES - 1:
                time.sleep(RETRY_BACKOFF * (attempt + 1))
    assert last is not None
    raise last


def _pipe(src: socket.socket, dst: socket.socket) -> None:
    """单向搬字节，直到一端关闭。"""
    try:
        while True:
            data = src.recv(PIPE_BUFFER)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        # 只关写端：让对向的 _pipe 把剩下的数据搬完，避免过早截断响应。
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass


class _Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        client = self.request
        upstream: socket.socket | None = None
        try:
            client.settimeout(HANDSHAKE_TIMEOUT)
            ver, nmethods = _recv_exactly(client, 2)
            if ver != SOCKS_VERSION:
                return
            _recv_exactly(client, nmethods)  # 客户端支持的方式，我们一律免认证放行
            client.sendall(bytes([SOCKS_VERSION, AUTH_NONE]))

            ver, cmd, _, atyp = _recv_exactly(client, 4)
            if ver != SOCKS_VERSION:
                return
            if atyp == ATYP_IPV4:
                host = socket.inet_ntoa(_recv_exactly(client, 4))
            elif atyp == ATYP_DOMAIN:
                host = _recv_exactly(client, _recv_exactly(client, 1)[0]).decode()
            elif atyp == ATYP_IPV6:
                host = socket.inet_ntop(socket.AF_INET6, _recv_exactly(client, 16))
            else:
                self._reply(client, REP_ATYP_NOT_SUPPORTED)
                return
            port = struct.unpack("!H", _recv_exactly(client, 2))[0]

            if cmd != CMD_CONNECT:
                self._reply(client, REP_CMD_NOT_SUPPORTED)
                return

            try:
                upstream = _connect_upstream(self.server.upstream_cfg, host, port)
            except UpstreamError as exc:
                # 探测出口 IP 会挨个试几个站点，前面的失败属于正常降级，
                # 报出来只会让用户以为代理坏了。
                if not self.server.quiet.is_set():
                    self.server.on_error(f"{host}:{port} → {exc}")
                self._reply(client, exc.rep)
                return

            self._reply(client, REP_SUCCESS)
            client.settimeout(None)

            # 双向转发。一个方向放到后台线程，另一个方向留在当前线程，
            # 这样 handle() 返回时隧道确实结束了（socketserver 会跟着关连接）。
            back = threading.Thread(target=_pipe, args=(upstream, client), daemon=True)
            back.start()
            _pipe(client, upstream)
            back.join()
        except (OSError, ConnectionError):
            pass
        finally:
            if upstream is not None:
                try:
                    upstream.close()
                except OSError:
                    pass

    @staticmethod
    def _reply(client: socket.socket, rep: int) -> None:
        try:
            client.sendall(bytes([SOCKS_VERSION, rep, 0x00, ATYP_IPV4, 0, 0, 0, 0, 0, 0]))
        except OSError:
            pass


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


class SocksRelay:
    """本地免认证 SOCKS5 中继，把流量转给带凭据的上游 SOCKS5 代理。

        with SocksRelay("socks5://user:pass@host:1080") as relay:
            launch_browser(proxy={"server": relay.local_url})

    只监听 127.0.0.1——免认证的口对外开放等于开放代理。
    """

    host = "127.0.0.1"

    def __init__(self, upstream_url: str, *, on_error=None):
        self._cfg = self._parse_upstream(upstream_url)
        self._on_error = on_error or (lambda msg: None)
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None
        self.port: int | None = None

    @staticmethod
    def _parse_upstream(url: str) -> dict:
        parts = urlsplit(url)
        if not parts.hostname or parts.port is None:
            raise ValueError(f"上游代理地址不完整：{url!r}")
        return {
            "host": parts.hostname,
            "port": parts.port,
            "username": unquote(parts.username or ""),
            "password": unquote(parts.password or ""),
        }

    @property
    def local_url(self) -> str:
        """给 Playwright 用的地址。免认证，所以不会触发浏览器那条 authentication 报错。"""
        if self.port is None:
            raise RuntimeError("中继尚未启动")
        return f"socks5://{self.host}:{self.port}"

    def start(self) -> SocksRelay:
        server = _Server((self.host, 0), _Handler)
        server.upstream_cfg = self._cfg
        server.on_error = self._on_error
        server.quiet = threading.Event()
        self._server = server
        self.port = server.server_address[1]
        self._thread = threading.Thread(target=server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def exit_ip(self) -> str | None:
        """经中继查出口 IP，查不到返回 None。

        必须走中继：本地 DNS 可能被 fake-ip 污染，本地直查会得到 198.18.x.x
        这类虚拟地址。camoufox 的 geoip=True 就是栽在这上面——它用本地解析的
        地址去 CONNECT，上游认不得，直接关连接。

        挨个试几个站点：上游对个别目标可能直接拒（实测 api.ipify.org 会被拒，
        icanhazip.com 正常），所以单个站点失败不代表代理有问题。
        """
        if self._server is None:
            raise RuntimeError("中继尚未启动")
        self._server.quiet.set()  # 探测期间的失败是正常降级，不往外报
        try:
            for host in EXIT_IP_HOSTS:
                try:
                    ip = self._fetch_ip(host)
                except Exception:
                    continue
                if ip and _looks_like_ipv4(ip):
                    return ip
            return None
        finally:
            self._server.quiet.clear()

    def _fetch_ip(self, host: str) -> str | None:
        s = socket.create_connection((self.host, self.port), EXIT_IP_TIMEOUT)
        try:
            s.settimeout(EXIT_IP_TIMEOUT)
            s.sendall(bytes([SOCKS_VERSION, 0x01, AUTH_NONE]))
            if _recv_exactly(s, 2)[1] != AUTH_NONE:
                return None
            d = host.encode()
            s.sendall(
                bytes([SOCKS_VERSION, CMD_CONNECT, 0x00, ATYP_DOMAIN, len(d)])
                + d
                + struct.pack("!H", self._exit_ip_port)
            )
            _, rep, _, atyp = _recv_exactly(s, 4)
            if atyp == ATYP_IPV4:
                _recv_exactly(s, 4)
            elif atyp == ATYP_DOMAIN:
                _recv_exactly(s, _recv_exactly(s, 1)[0])
            elif atyp == ATYP_IPV6:
                _recv_exactly(s, 16)
            _recv_exactly(s, 2)
            if rep != REP_SUCCESS:
                return None

            conn = self._wrap_tls(s, host)
            conn.sendall(
                f"GET / HTTP/1.1\r\nHost: {host}\r\n"
                "User-Agent: curl/8\r\nConnection: close\r\n\r\n".encode()
            )
            raw = b""
            while len(raw) < 8192:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                raw += chunk
            head, _, body = raw.partition(b"\r\n\r\n")
            if b" 200 " not in head.split(b"\r\n")[0]:
                return None
            return body.strip().decode(errors="ignore").splitlines()[-1].strip()
        finally:
            try:
                s.close()
            except OSError:
                pass

    # 这两个是接缝，测试里替换掉即可用明文假上游验证 SOCKS 协议路径本身，
    # 不必为了跑测试去签一套自签证书。
    _exit_ip_port = 443

    @staticmethod
    def _wrap_tls(sock: socket.socket, host: str):
        import ssl

        return ssl.create_default_context().wrap_socket(sock, server_hostname=host)

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        self.port = None

    def __enter__(self) -> SocksRelay:
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()
