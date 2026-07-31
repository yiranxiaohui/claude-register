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

import os
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

# 上游握手（连接 + 认证 + CONNECT）的超时。握手阶段被挂住多半是撞了上游的
# 并发限额（实测超限连接是「挂住不回复」而非快速 EOF，见 DEFAULT_MAX_UPSTREAM
# 的说明），槽位周转很快，换条新连接重试大概率能过。取值让「挂满一次 +
# backoff + 完整再试一次」能装进 RETRY_DEADLINE：5 + 0.4 + 5 ≈ 10.4s < 12s。
HANDSHAKE_TIMEOUT = 5.0
PIPE_BUFFER = 65536

# 查出口 IP 用的站点。走中继查（域名交给上游解析），因为本地 DNS 可能被
# fake-ip 污染——Clash 一类透明代理会把域名解析成 198.18.x.x，本地直查得到的
# 是虚拟地址，拿去 CONNECT 上游只会被拒。
EXIT_IP_HOSTS = ("api.ipify.org", "checkip.amazonaws.com", "icanhazip.com")
EXIT_IP_TIMEOUT = 12.0

# 机场类代理常有并发连接上限（实测这个上游约 4 条），超限的连接会在握手阶段
# 被拒（EOF）或被挂住不回复（撞 HANDSHAKE_TIMEOUT）。但槽位随着旧连接关闭很快
# 释放，短暂重试就能过——一次失败就放弃会在注册流程中途白掉请求。只对「握手
# 阶段被拒/挂住」重试，CONNECT 已经被上游明确拒绝（rep != 0）说明是目标本身
# 的问题，重试没有意义。
#
# 重试总耗时必须远小于 page.goto 的 60s 导航超时：否则上游 hang 住时浏览器先超时，
# 用户看到的仍是 NS_ERROR_NET_TIMEOUT，而真正有用的报错还没来得及冒出来。
# RETRY_DEADLINE 是墙钟硬上限：挂住型失败一次就吃掉一个 HANDSHAKE_TIMEOUT，
# 预算内装得下两次完整尝试；真的连不上就该早点认输。
CONNECT_RETRIES = 4
RETRY_BACKOFF = 0.4
RETRY_DEADLINE = 12.0

# SOCKS5 认证子协商里用户名/密码都是单字节长度前缀，最长 255。
MAX_CREDENTIAL_LEN = 255

# 上游并发上限。机场类上游有硬并发连接数上限（实测这个上游约 4 条），超限的
# 连接会被挂住（不回复），中继若无限并发转发就会超订上游：claude.ai 登录页
# 十几条并发会把关键连接（hcaptcha、验证码提交）挤掉，表现为满屏「上游握手
# 超时」且注册偶发失败。用信号量把中继对上游的并发闸到这个值，超出的连接在
# 本地排队等槽位而不是冲上游被挂死。可用环境变量 RELAY_MAX_UPSTREAM 覆盖。
# 默认取 3 而非实测的 ~4：留一格余量，免得上游偶尔只放 3 条（关连接的 TIME_WAIT
# 残留、服务端抖动）时又擦边超订、重新触发 hang。确知上游能吃满 4 条时可上调。
DEFAULT_MAX_UPSTREAM = 3

# 等本地槽位的上限。排队是常态，给足耐心；但不能无限等——真出现死锁式占满
# 时，超过这个时间就回一个明确的失败码，好过让浏览器一路挂到导航超时。
SLOT_WAIT_TIMEOUT = 30.0


def _default_max_upstream() -> int:
    raw = os.environ.get("RELAY_MAX_UPSTREAM", "")
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_UPSTREAM
    return value if value > 0 else DEFAULT_MAX_UPSTREAM


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


def _connect_upstream_once(cfg: dict, host: str, port: int, timeout: float) -> socket.socket:
    """跟上游完成 SOCKS5 握手，返回已建好隧道的 socket。

    host 原样透传给上游解析（socks5h 语义）：本地 DNS 可能被污染，
    而且本地解析会泄露我们真实的地理位置。
    """
    try:
        up = socket.create_connection((cfg["host"], cfg["port"]), timeout)
    except TimeoutError as exc:
        # TCP 都连不上说明上游主机/网络有问题（并发限额挂的是握手回复，
        # 不影响 TCP accept）——重试只会把导航超时耗光。
        raise UpstreamError(f"连上游代理超时：{exc}", REP_HOST_UNREACHABLE) from exc
    except OSError as exc:
        raise UpstreamError(
            f"连不上上游代理：{exc}", REP_HOST_UNREACHABLE, retryable=True
        ) from exc

    up.settimeout(timeout)
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
    except TimeoutError as exc:
        up.close()
        # 握手途中被挂住不回复——实测正是撞并发限额的表现（上游把超限连接
        # hang 住而非 EOF）。槽位周转快，换条新连接重试大概率能过；重试的
        # 墙钟预算由 _connect_upstream 的 RETRY_DEADLINE 兜底，不会耗光
        # 浏览器的导航超时。
        raise UpstreamError(f"上游握手超时：{exc}", retryable=True) from exc
    except (OSError, ConnectionError) as exc:
        up.close()
        # 握手途中被 EOF——典型的撞并发限额表现，值得重试。
        raise UpstreamError(f"上游握手失败：{exc}", retryable=True) from exc

    up.settimeout(None)
    return up


def _connect_upstream(cfg: dict, host: str, port: int) -> socket.socket:
    """带重试的上游连接。只重试握手阶段的失败（多半是并发限额），
    上游明确拒绝的目标不重试。

    重试受 RETRY_DEADLINE 墙钟约束：EOF 型失败是毫秒级返回的，挂住型失败
    一次就吃掉一个 HANDSHAKE_TIMEOUT——后续尝试的超时按剩余预算收紧，
    保证总耗时不越过 deadline 太多，不把浏览器的导航超时耗光。
    """
    deadline = time.monotonic() + RETRY_DEADLINE
    last: UpstreamError | None = None
    for attempt in range(CONNECT_RETRIES):
        # 首次给完整超时；重试按剩余预算收紧，免得多次挂住把墙钟撑爆。
        remaining = deadline - time.monotonic()
        if attempt and remaining <= RETRY_BACKOFF:
            break
        timeout = HANDSHAKE_TIMEOUT if attempt == 0 else min(HANDSHAKE_TIMEOUT, remaining)
        try:
            return _connect_upstream_once(cfg, host, port, timeout)
        except UpstreamError as exc:
            last = exc
            if not exc.retryable:
                raise
            if attempt == CONNECT_RETRIES - 1:
                break
            backoff = RETRY_BACKOFF * (attempt + 1)
            if time.monotonic() + backoff >= deadline:
                break
            time.sleep(backoff)
    if last is None:  # pragma: no cover - 循环至少跑一轮，必有 last
        raise UpstreamError("上游连接失败")
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
        replied = False  # CONNECT 回过之后就进数据流了，不能再往里塞协议字节
        slot_held = False  # 是否占着上游并发槽位，决定 finally 里要不要 release
        self.server.track(client)
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
                raw = _recv_exactly(client, _recv_exactly(client, 1)[0])
                try:
                    # RFC 1928 的域名字段是 ASCII（非 ASCII 域名走 punycode）。
                    # 解不出来就明确回错误码——绝不能让 UnicodeDecodeError 把
                    # handler 打死，那样客户端一个字节都收不到，只能挂到导航
                    # 超时，正是这次要修的症状。
                    host = raw.decode("ascii")
                except UnicodeDecodeError:
                    self._reply(client, REP_HOST_UNREACHABLE)
                    return
            elif atyp == ATYP_IPV6:
                host = socket.inet_ntop(socket.AF_INET6, _recv_exactly(client, 16))
            else:
                self._reply(client, REP_ATYP_NOT_SUPPORTED)
                return
            port = struct.unpack("!H", _recv_exactly(client, 2))[0]

            if cmd != CMD_CONNECT:
                self._reply(client, REP_CMD_NOT_SUPPORTED)
                return

            # 占一个上游并发槽位，全程持有到隧道结束——上游的限额是对「同时打开
            # 的连接数」而言，只在握手期占位挡不住后面并发的数据连接超订。拿不到
            # 槽位就在本地排队等，超过 SLOT_WAIT_TIMEOUT 才认输回失败码。
            if not self.server.upstream_slots.acquire(timeout=SLOT_WAIT_TIMEOUT):
                if not self.server.quiet.is_set():
                    self.server.on_error(
                        f"{host}:{port} → 等待上游并发槽位超时（{SLOT_WAIT_TIMEOUT:.0f}s，"
                        "上游并发已满）"
                    )
                self._reply(client, REP_HOST_UNREACHABLE)
                return
            slot_held = True

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
            replied = True
            client.settimeout(None)
            # 上游侧也要登记：反向 _pipe 阻塞在 upstream.recv 上，只掐客户端
            # 那一头的话 back.join() 仍会卡住，整个 handler 还是活的。
            self.server.track(upstream)

            # 双向转发。一个方向放到后台线程，另一个方向留在当前线程，
            # 这样 handle() 返回时隧道确实结束了（socketserver 会跟着关连接）。
            back = threading.Thread(target=_pipe, args=(upstream, client), daemon=True)
            back.start()
            _pipe(client, upstream)
            back.join()
        except (OSError, ConnectionError):
            pass
        except Exception as exc:
            # 兜底：任何没预料到的异常都要先回一个错误码再退场。静默断开会让
            # 浏览器一路挂到导航超时，报出来的还是没信息量的 NS_ERROR_NET_TIMEOUT。
            if not self.server.quiet.is_set():
                self.server.on_error(f"中继内部错误：{exc!r}")
            if not replied:
                # 已经回过 CONNECT 成功的话，这条连接上跑的是应用数据，
                # 再补一个 SOCKS 回复就是往数据流里掺垃圾。
                self._reply(client, REP_GENERAL_FAILURE)
        finally:
            self.server.untrack(client)
            if upstream is not None:
                self.server.untrack(upstream)
                try:
                    upstream.close()
                except OSError:
                    pass
            # 隧道彻底收尾后再释放槽位，让等待的连接接手一个真正空出来的槽。
            if slot_held:
                self.server.upstream_slots.release()

    @staticmethod
    def _reply(client: socket.socket, rep: int) -> None:
        try:
            client.sendall(bytes([SOCKS_VERSION, rep, 0x00, ATYP_IPV4, 0, 0, 0, 0, 0, 0]))
        except OSError:
            pass


class _Server(socketserver.ThreadingTCPServer):
    # daemon_threads=True 会让 ThreadingMixIn 干脆不跟踪线程，于是 server_close()
    # 里那次 join 是空操作——已经建好的隧道会活过 stop()，带着上游的认证连接
    # 继续占着并发槽位。serve.py 是长期进程，这种残留会一路把上游吃满。
    # 所以自己记一份在跑的连接，stop() 时挨个掐断。
    daemon_threads = True
    allow_reuse_address = True
    # 默认 5 太小：Firefox 加载一个页面会并行开十几条连接，backlog 满了之后
    # 新连接会被内核直接拒（或干脆丢弃等重传），表现就是页面偶发加载不全。
    request_queue_size = 128

    def __init__(self, *args, max_upstream: int = DEFAULT_MAX_UPSTREAM, **kwargs):
        self._live: set[socket.socket] = set()
        self._live_lock = threading.Lock()
        # 限中继对上游的并发连接数，匹配上游的硬上限，避免超订。
        self.upstream_slots = threading.BoundedSemaphore(max_upstream)
        super().__init__(*args, **kwargs)

    def track(self, sock: socket.socket) -> None:
        with self._live_lock:
            self._live.add(sock)

    def untrack(self, sock: socket.socket) -> None:
        with self._live_lock:
            self._live.discard(sock)

    def close_live(self) -> None:
        """掐断所有在跑的隧道。

        先 shutdown 后 close，两步都要：POSIX 上 shutdown(SHUT_RDWR) 就能唤醒
        阻塞在 recv 的 _pipe；Windows 上实测 shutdown 唤不醒（recv 一直挂着），
        必须 close 才会抛 ConnectionAbortedError。

        顺序不能反：先 shutdown 让读线程醒过来退出，再 close 收 fd，避免
        「另一个线程还阻塞在这个 fd 上」的窗口。close 走的是 Python socket
        对象，重复 close 是幂等的，handler 的 finally 再关一次没有副作用。
        """
        with self._live_lock:
            live = list(self._live)
        for sock in live:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        for sock in live:
            try:
                sock.close()
            except OSError:
                pass


class SocksRelay:
    """本地免认证 SOCKS5 中继，把流量转给带凭据的上游 SOCKS5 代理。

        with SocksRelay("socks5://user:pass@host:1080") as relay:
            launch_browser(proxy={"server": relay.local_url})

    只监听 127.0.0.1——免认证的口对外开放等于开放代理。
    """

    host = "127.0.0.1"

    def __init__(self, upstream_url: str, *, on_error=None, max_upstream: int | None = None):
        self._cfg = self._parse_upstream(upstream_url)
        self._on_error = on_error or (lambda msg: None)
        self._max_upstream = (
            max_upstream if max_upstream and max_upstream > 0 else _default_max_upstream()
        )
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None
        self.port: int | None = None

    @staticmethod
    def _parse_upstream(url: str) -> dict:
        parts = urlsplit(url)
        if not parts.hostname or parts.port is None:
            # 不要把 url 原样带进消息——里面有凭据，而这条报错会落进
            # 网页可读的 log.txt。
            raise ValueError("上游代理地址不完整：缺少主机或端口")
        cfg = {
            "host": parts.hostname,
            "port": parts.port,
            "username": unquote(parts.username or ""),
            "password": unquote(parts.password or ""),
        }
        for field, label in (("username", "用户名"), ("password", "密码")):
            n = len(cfg[field].encode())
            if n > MAX_CREDENTIAL_LEN:
                # SOCKS5 认证子协商是单字节长度前缀。放过去的话运行时
                # bytes([n]) 会抛 ValueError 把 handler 打死，客户端只能挂到超时。
                raise ValueError(
                    f"代理{label}过长（{n} 字节），SOCKS5 最多 {MAX_CREDENTIAL_LEN} 字节"
                )
        return cfg

    @property
    def local_url(self) -> str:
        """给 Playwright 用的地址。免认证，所以不会触发浏览器那条 authentication 报错。"""
        if self.port is None:
            raise RuntimeError("中继尚未启动")
        return f"socks5://{self.host}:{self.port}"

    def start(self) -> SocksRelay:
        server = _Server((self.host, 0), _Handler, max_upstream=self._max_upstream)
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
        # ssl.wrap_socket 会把 s detach 掉（s.fileno() 变 -1），之后关 s 是空操作，
        # 真正持有 fd 的是包装后那个对象。所以记下它，在 finally 里关它。
        conn: socket.socket | None = None
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
            for sock in (conn, s):
                if sock is None:
                    continue
                try:
                    sock.close()
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
            self._server.shutdown()  # 停 accept 循环
            self._server.close_live()  # 掐断已建立的隧道，否则它们会活过 stop()
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
