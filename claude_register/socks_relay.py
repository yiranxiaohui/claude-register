"""本地免认证 SOCKS5 中继。

为什么需要它：Chromium / Playwright 明确不支持 SOCKS5 用户名密码认证——
playwright driver 的 `normalizeProxySettings` 里就一句

    if (url.protocol === "socks5:" && (proxy.username || proxy.password))
      throw new Error(`Browser does not support socks5 proxy authentication`);

而机场类代理基本都是带凭据的。于是在本地开一个免认证 SOCKS5 口，浏览器连
127.0.0.1，中继替它跟上游完成认证。Playwright 内部对付同类问题用的也是这招
（coreBundle.js 里的 `socks5://127.0.0.1:${this._socksProxy.port()}`）。

只实现 CONNECT + 域名/IPv4 地址类型——浏览器代理场景用不到 BIND / UDP ASSOCIATE。
"""

from __future__ import annotations

import errno
import os
import select
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
REP_NOT_ALLOWED = 0x02
REP_HOST_UNREACHABLE = 0x04
REP_CMD_NOT_SUPPORTED = 0x07
REP_ATYP_NOT_SUPPORTED = 0x08

# 单次尝试的总预算，覆盖解析上游地址、TCP、认证和 CONNECT。
# 每次读写都使用同一截止时间，分段返回的数据不能不断延长预算。
HANDSHAKE_TIMEOUT = 5.0
PIPE_BUFFER = 65536

# 查出口 IP 用的站点。走中继查（域名交给上游解析），因为本地 DNS 可能被
# fake-ip 污染——Clash 一类透明代理会把域名解析成 198.18.x.x，本地直查得到的
# 是虚拟地址，拿去 CONNECT 上游只会被拒。
EXIT_IP_HOSTS = ("api.ipify.org", "checkip.amazonaws.com", "icanhazip.com")
EXIT_IP_TIMEOUT = 12.0

# 对暂时的握手失败做有限重试。透明网关接收 TCP 后仍可能连不上真实上游，
# 因此 EOF/握手超时本身不能证明上游有并发限额。全部尝试与退避共用总预算。
CONNECT_RETRIES = 4
RETRY_BACKOFF = 0.4
RETRY_DEADLINE = 12.0

# SOCKS5 认证子协商里用户名/密码都是单字节长度前缀，最长 255。
MAX_CREDENTIAL_LEN = 255

# 给浏览器多个来源的长连接留出空间，可通过 RELAY_MAX_UPSTREAM 调整。
# 接管浏览器单独配置并发；实际容量需要按节点验证，不能从超时报错推断。
DEFAULT_MAX_UPSTREAM = 16

# SOCKS 隧道无法区分闲置连接与正在等待响应的 TLS/SSE 请求，不能按静默时间
# 掐断连接来腾出槽位。排队、连接、握手和退避期间定期检查取消状态。
CANCEL_POLL = 0.1
# getaddrinfo 没有可移植的取消 API。解析放到有界 daemon worker 中；请求超时或
# 取消时立即退出等待，晚到的解析结果不会再触发连接，也不会无限堆积解析线程。
_DNS_SLOTS = threading.BoundedSemaphore(8)

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


class _Cancelled(Exception):
    """中继已停止或本地客户端已放弃请求，不再重试、也不报网络故障。"""


def _noop(*args) -> None:
    pass


def _remaining(deadline: float, check_cancelled=_noop) -> float:
    check_cancelled()
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("已达到连接截止时间")
    return remaining


def _wait_socket(sock: socket.socket, deadline: float, check_cancelled, *, write=False):
    while True:
        timeout = min(CANCEL_POLL, _remaining(deadline, check_cancelled))
        try:
            readable, writable, errors = select.select(
                [] if write else [sock], [sock] if write else [], [sock], timeout,
            )
        except (OSError, ValueError):
            check_cancelled()
            raise
        _remaining(deadline, check_cancelled)
        if readable or writable or errors:
            return


def _sendall(sock: socket.socket, data: bytes, deadline: float, check_cancelled):
    pending = memoryview(data)
    while pending:
        _wait_socket(sock, deadline, check_cancelled, write=True)
        try:
            sent = sock.send(pending)
        except BlockingIOError:
            continue
        if not sent:
            raise ConnectionError("上游提前关闭")
        pending = pending[sent:]


def _recv_exactly(sock: socket.socket, n: int, *, deadline=None, check_cancelled=_noop) -> bytes:
    """收满 n 字节。收不满说明对端提前关了，抛 ConnectionError。

    SOCKS 是长度前缀协议，短读会让后续解析全部错位——必须收满。
    """
    chunks: list[bytes] = []
    remaining = n
    while remaining > 0:
        if deadline is not None:
            _wait_socket(sock, deadline, check_cancelled)
        try:
            chunk = sock.recv(remaining)
        except BlockingIOError:
            if deadline is None:
                raise
            continue
        if not chunk:
            raise ConnectionError(f"对端提前关闭，还差 {remaining} 字节")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _resolve_upstream(cfg: dict, deadline: float, check_cancelled):
    while not _DNS_SLOTS.acquire(timeout=min(CANCEL_POLL, _remaining(deadline, check_cancelled))):
        pass
    done = threading.Event()
    result = []

    def resolve():
        try:
            result.append(socket.getaddrinfo(cfg["host"], cfg["port"], type=socket.SOCK_STREAM))
        except Exception as exc:
            result.append(exc)
        finally:
            _DNS_SLOTS.release()
            done.set()

    try:
        _remaining(deadline, check_cancelled)
        threading.Thread(target=resolve, daemon=True).start()
    except BaseException:
        _DNS_SLOTS.release()
        raise
    while not done.wait(min(CANCEL_POLL, _remaining(deadline, check_cancelled))):
        pass
    _remaining(deadline, check_cancelled)
    if isinstance(result[0], Exception):
        raise result[0]
    return result[0]


class UpstreamError(Exception):
    """连接/认证上游失败。携带该回给客户端的 SOCKS rep code。"""

    def __init__(self, message: str, rep: int = REP_GENERAL_FAILURE, *, retryable: bool = False):
        super().__init__(message)
        self.rep = rep
        # 暂时的网络失败可以重试；明确的认证/CONNECT 拒绝直接返回。
        self.retryable = retryable


def _connect_upstream_once(
    cfg: dict, host: str, port: int, deadline: float, *,
    check_cancelled=_noop, track=_noop, untrack=_noop,
) -> socket.socket:
    """跟上游完成 SOCKS5 握手，返回已建好隧道的 socket。

    host 原样透传给上游解析（socks5h 语义）：本地 DNS 可能被污染，
    而且本地解析会泄露我们真实的地理位置。
    """
    up = None
    stage = "解析上游地址"
    try:
        addresses = _resolve_upstream(cfg, deadline, check_cancelled)
        stage = "TCP 连接"
        last_error = OSError("上游地址没有可用的 TCP 结果")
        for family, kind, proto, _, address in addresses:
            _remaining(deadline, check_cancelled)
            up = socket.socket(family, kind, proto)
            track(up)  # 从 TCP 连接前开始登记，停止中继能关掉握手中的 socket。
            up.setblocking(False)
            try:
                err = up.connect_ex(address)
                if err in (errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY, errno.EINTR):
                    _wait_socket(up, deadline, check_cancelled, write=True)
                    err = up.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if err:
                    raise OSError(err, os.strerror(err))
                break
            except OSError as exc:
                check_cancelled()
                untrack(up)
                up.close()
                up = None
                last_error = exc
        else:
            raise last_error

        def send(data):
            _sendall(up, data, deadline, check_cancelled)

        def recv(n):
            return _recv_exactly(up, n, deadline=deadline, check_cancelled=check_cancelled)

        stage = "协商认证方式"
        want_auth = bool(cfg.get("username") or cfg.get("password"))
        methods = [AUTH_NONE, AUTH_USER_PASS] if want_auth else [AUTH_NONE]
        send(bytes([SOCKS_VERSION, len(methods)]) + bytes(methods))
        ver, method = recv(2)
        if ver != SOCKS_VERSION:
            raise UpstreamError(f"上游不是 SOCKS5（版本 {ver}）")
        if method == AUTH_UNACCEPTABLE:
            raise UpstreamError("上游拒绝了所有认证方式")
        if method == AUTH_USER_PASS:
            stage = "用户名密码认证"
            user = cfg.get("username", "").encode()
            pwd = cfg.get("password", "").encode()
            send(b"\x01" + bytes([len(user)]) + user + bytes([len(pwd)]) + pwd)
            _, status = recv(2)
            if status != 0x00:
                raise UpstreamError("上游认证失败：用户名或密码不对")
        elif method != AUTH_NONE:
            raise UpstreamError(f"上游要求不支持的认证方式 {method}")

        stage = "CONNECT 目标"
        d = host.encode()
        send(
            bytes([SOCKS_VERSION, CMD_CONNECT, 0x00, ATYP_DOMAIN, len(d)])
            + d
            + struct.pack("!H", port)
        )
        _, rep, _, atyp = recv(4)
        if atyp == ATYP_IPV4:
            recv(4)
        elif atyp == ATYP_DOMAIN:
            recv(recv(1)[0])
        elif atyp == ATYP_IPV6:
            recv(16)
        recv(2)  # 绑定端口
        if rep != REP_SUCCESS:
            raise UpstreamError(f"上游拒绝 CONNECT {host}:{port}（rep={rep}）", rep)
        _remaining(deadline, check_cancelled)
        up.settimeout(None)
        return up
    except BaseException as exc:
        if up is not None:
            untrack(up)
            up.close()
        check_cancelled()
        if isinstance(exc, TimeoutError):
            raise UpstreamError(
                f"上游连接超时（{stage}）：{exc}",
                REP_HOST_UNREACHABLE, retryable=stage != "TCP 连接",
            ) from exc
        if isinstance(exc, OSError):
            raise UpstreamError(f"上游连接失败（{stage}）：{exc}", retryable=True) from exc
        raise


def _connect_upstream(
    cfg: dict, host: str, port: int, *, check_cancelled=_noop, track=_noop, untrack=_noop,
) -> socket.socket:
    """全部尝试、DNS、握手与退避共用 RETRY_DEADLINE，取消后不再发起连接。"""
    deadline = time.monotonic() + RETRY_DEADLINE
    last: UpstreamError | None = None
    for attempt in range(CONNECT_RETRIES):
        check_cancelled()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        attempt_deadline = min(deadline, time.monotonic() + HANDSHAKE_TIMEOUT)
        try:
            return _connect_upstream_once(
                cfg, host, port, attempt_deadline,
                check_cancelled=check_cancelled, track=track, untrack=untrack,
            )
        except UpstreamError as exc:
            last = exc
            if not exc.retryable:
                raise
            if attempt == CONNECT_RETRIES - 1:
                break
            backoff = RETRY_BACKOFF * (attempt + 1)
            if time.monotonic() + backoff >= deadline:
                break
            wake_at = time.monotonic() + backoff
            while time.monotonic() < wake_at:
                check_cancelled()
                time.sleep(min(CANCEL_POLL, max(0, wake_at - time.monotonic())))
    if last is None:  # pragma: no cover - 循环至少跑一轮，必有 last
        raise UpstreamError("上游连接失败")
    raise last


def _host_matches(host: str, patterns) -> bool:
    """host 等于某个模式，或是它的子域名（不区分大小写）。"""
    host = host.lower().rstrip(".")
    return any(host == p or host.endswith("." + p) for p in patterns)


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
    def _check_cancelled(self) -> None:
        if self.server.stopping.is_set():
            raise _Cancelled()
        # 排队与握手时没有其他线程读取客户端。只窥视、不消费可能提前发送的
        # TLS 数据；EOF/reset 才代表客户端已放弃，不能继续占槽位重试。
        try:
            readable, _, _ = select.select([self.request], [], [], 0)
            if readable and not self.request.recv(1, socket.MSG_PEEK):
                raise _Cancelled()
        except (OSError, ValueError):
            raise _Cancelled() from None

    def handle(self) -> None:
        client = self.request
        upstream: socket.socket | None = None
        replied = False  # CONNECT 回过之后就进数据流了，不能再往里塞协议字节
        slot_held = False  # 是否占着上游并发槽位，决定 finally 里要不要 release
        try:
            self.server.track(client)
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

            # 拒绝名单（浏览器后台服务、统计追踪）当场回绝，不占上游槽位：
            # 这类连接对注册/接管毫无用处，却会长期钉住稀缺的上游并发。
            if _host_matches(host, self.server.deny_hosts):
                self._reply(client, REP_NOT_ALLOWED)
                return

            # 占一个上游并发槽位，全程持有到隧道结束——上游的限额是对「同时打开
            # 的连接数」而言，只在握手期占位挡不住后面并发的数据连接超订。拿不到
            # 槽位就在本地排队等，超过 SLOT_WAIT_TIMEOUT 才认输回失败码。
            if not self.server.acquire_slot(SLOT_WAIT_TIMEOUT, self._check_cancelled):
                if not self.server.quiet.is_set():
                    self.server.on_error(
                        f"{host}:{port} → 等待上游并发槽位超时（{SLOT_WAIT_TIMEOUT:.0f}s，"
                        "本地中继并发已满）"
                    )
                self._reply(client, REP_HOST_UNREACHABLE)
                return
            slot_held = True

            try:
                upstream = _connect_upstream(
                    self.server.upstream_cfg, host, port,
                    check_cancelled=self._check_cancelled,
                    track=self.server.track, untrack=self.server.untrack,
                )
            except UpstreamError as exc:
                # 探测出口 IP 会挨个试几个站点，前面的失败属于正常降级，
                # 报出来只会让用户以为代理坏了。
                if not self.server.quiet.is_set():
                    self.server.on_error(f"{host}:{port} → {exc}")
                self._reply(client, exc.rep)
                return

            self._check_cancelled()
            self._reply(client, REP_SUCCESS)
            replied = True
            client.settimeout(None)
            # 双向转发。一个方向放到后台线程，另一个方向留在当前线程，
            # 这样 handle() 返回时隧道确实结束了（socketserver 会跟着关连接）。
            back = threading.Thread(target=_pipe, args=(upstream, client), daemon=True)
            back.start()
            _pipe(client, upstream)
            back.join()
        except (_Cancelled, OSError):
            pass
        except Exception as exc:
            # 兜底：任何没预料到的异常都要先回一个错误码再退场。静默断开会让
            # 浏览器一路挂到导航超时，报出来的还是没信息量的超时错误。
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
    # 默认 5 太小：浏览器加载一个页面会并行开十几条连接，backlog 满了之后
    # 新连接会被内核直接拒（或干脆丢弃等重传），表现就是页面偶发加载不全。
    request_queue_size = 128

    def __init__(self, *args, max_upstream: int = DEFAULT_MAX_UPSTREAM, deny_hosts=(), **kwargs):
        self._live: set[socket.socket] = set()
        self._live_lock = threading.Lock()
        self.stopping = threading.Event()
        # 限制本中继同时打开的上游连接数；这不是对节点实际容量的探测。
        self.upstream_slots = threading.BoundedSemaphore(max_upstream)
        self.deny_hosts = tuple(h.lower().rstrip(".") for h in deny_hosts)
        super().__init__(*args, **kwargs)

    def acquire_slot(self, timeout: float, check_cancelled=_noop) -> bool:
        """只等待已有隧道自然释放槽位，取消时退出，不中断其他请求。"""
        deadline = time.monotonic() + timeout
        while True:
            check_cancelled()
            if self.stopping.is_set():
                raise _Cancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            if self.upstream_slots.acquire(timeout=min(CANCEL_POLL, remaining)):
                try:
                    check_cancelled()
                    if self.stopping.is_set():
                        raise _Cancelled()
                except BaseException:
                    self.upstream_slots.release()
                    raise
                return True

    def track(self, sock: socket.socket) -> None:
        with self._live_lock:
            if self.stopping.is_set():
                raise _Cancelled()
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
            self.stopping.set()
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

    def __init__(self, upstream_url: str, *, on_error=None, max_upstream: int | None = None,
                 deny_hosts=()):
        self._cfg = self._parse_upstream(upstream_url)
        self._on_error = on_error or (lambda msg: None)
        self._deny_hosts = tuple(deny_hosts)
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
        server = _Server(
            (self.host, 0), _Handler, max_upstream=self._max_upstream,
            deny_hosts=self._deny_hosts,
        )
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
        这类虚拟地址；拿本地解析出的
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
            self._server.close_live()  # 先取消排队、连接和握手，禁止新 socket 登记。
            self._server.shutdown()  # 停 accept 循环
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
