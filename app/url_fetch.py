"""Fetch user-supplied URLs without allowing SSRF.

Only http/https are allowed, and every connection (including each redirect hop)
resolves the host, rejects any non-public address, and connects to exactly the
address that was checked, so DNS rebinding cannot swap in an internal IP.

The whole fetch (DNS, connects, TLS, headers, redirects and body) runs against
one wall-clock deadline. DNS lookups stop being waited on when it runs out, and
a watchdog shuts down every socket the fetch opened, which unblocks whatever
read or handshake is in progress, however slowly the server is trickling bytes.
"""

import contextlib
import http.client
import ipaddress
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

ALLOWED_SCHEMES = ("http", "https")
# Upper bound for any single connect/recv.
FETCH_TIMEOUT = 10
# Wall-clock budget for the whole fetch: DNS, connects, TLS, redirects and body.
FETCH_DEADLINE = 30
# getaddrinfo cannot be cancelled, so a lookup that outlives its fetch keeps its
# thread until the system resolver gives up. This caps how many can pile up.
# Known trade-off: anyone who controls a nameserver that never answers can fill
# every slot (the cap, like every limit here, is per worker process), and URL
# uploads then fail until the system resolver times those
# lookups out (resolv.conf timeout x attempts x nameservers; ~10-30s on glibc
# defaults). File uploads are unaffected. Per-IP rate and concurrency limits in
# app.py bound how often one client can do this; a deployment that cares more
# should point the system resolver at a caching resolver with short timeouts.
MAX_PENDING_LOOKUPS = 16
USER_AGENT = "imgpush (+https://github.com/hauxir/imgpush)"

_lookup_slots = threading.BoundedSemaphore(MAX_PENDING_LOOKUPS)

# One context for every fetch: building it loads the CA bundle, and http.client
# would otherwise build its own per connection. ALPN is pinned to HTTP/1.1
# because that is all http.client speaks.
_SSL_CONTEXT = ssl.create_default_context()
_SSL_CONTEXT.set_alpn_protocols(["http/1.1"])

# Explicit lists rather than ipaddress.is_global, whose ranges change between
# Python releases. Sources: IANA IPv4/IPv6 Special-Purpose Address Registries.
_BLOCKED_V4 = tuple(
    ipaddress.IPv4Network(n)
    for n in (
        "0.0.0.0/8",  # "this network"
        "10.0.0.0/8",  # private
        "100.64.0.0/10",  # carrier-grade NAT
        "127.0.0.0/8",  # loopback
        "169.254.0.0/16",  # link-local (cloud metadata)
        "172.16.0.0/12",  # private
        "192.0.0.0/24",  # IETF protocol assignments
        "192.0.2.0/24",  # documentation
        "192.88.99.0/24",  # 6to4 relay anycast
        "192.168.0.0/16",  # private
        "198.18.0.0/15",  # benchmarking
        "198.51.100.0/24",  # documentation
        "203.0.113.0/24",  # documentation
        "224.0.0.0/4",  # multicast
        "240.0.0.0/4",  # reserved, includes broadcast
    )
)
# Only global unicast (2000::/3) is public; everything else (loopback, ULA,
# link-local, multicast, ...) is rejected. These are the non-public parts of it.
# Tunnelling prefixes (Teredo, 6to4) are rejected outright: no real image host
# lives there and the address they lead to depends on the local network setup.
_GLOBAL_UNICAST_V6 = ipaddress.IPv6Network("2000::/3")
_BLOCKED_V6 = tuple(
    ipaddress.IPv6Network(n)
    for n in (
        "2001::/23",  # IETF protocol assignments, incl. Teredo (2001::/32)
        "2001:db8::/32",  # documentation
        "2002::/16",  # 6to4
        "3fff::/20",  # documentation
    )
)
_NAT64_PREFIX = ipaddress.IPv6Network("64:ff9b::/96")


class FetchError(Exception):
    """The URL could not be fetched. The message is safe to show to the user.

    status_code is the HTTP status our own response should carry: the remote
    server failing is a bad gateway, not a mistake in the client's request.
    """

    status_code = 502


class BlockedURLError(FetchError):
    """The URL is not allowed to be fetched at all."""

    status_code = 400


class FetchTimeoutError(FetchError):
    """The remote server did not deliver the file in time."""

    status_code = 504


class TooLargeError(FetchError):
    """The remote file is bigger than we accept."""

    status_code = 413


def _is_public_v4(addr: ipaddress.IPv4Address) -> bool:
    return not any(addr in net for net in _BLOCKED_V4)


def is_public_address(ip: str) -> bool:
    addr = ipaddress.ip_address(ip.split("%", 1)[0])
    if isinstance(addr, ipaddress.IPv4Address):
        return _is_public_v4(addr)
    # IPv4-mapped and well-known NAT64 addresses reach an IPv4 host directly
    # (via the local stack or a NAT64 gateway), so judge them by that address.
    if addr.ipv4_mapped is not None:
        return _is_public_v4(addr.ipv4_mapped)
    if addr in _NAT64_PREFIX:
        return _is_public_v4(ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF))
    return addr in _GLOBAL_UNICAST_V6 and not any(addr in net for net in _BLOCKED_V6)


class _Deadline:
    """One wall-clock budget for a fetch, enforced by a watchdog timer."""

    def __init__(self, seconds: float) -> None:
        self._end = time.monotonic() + seconds
        self._lock = threading.Lock()
        self._sockets: list[socket.socket] = []
        self.fired = False
        self._finished = False
        self._timer = threading.Timer(seconds, self._fire)
        self._timer.daemon = True

    def __enter__(self) -> "_Deadline":
        self._timer.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._timer.cancel()

    def _fire(self) -> None:
        with self._lock:
            if self._finished:
                return
            self.fired = True
            for sock in self._sockets:
                _shutdown(sock)

    def finish(self) -> bool:
        """Stop the watchdog for good; returns whether it had already fired.

        Taken under the lock so a fetch that completed just as the deadline
        ran out is judged once, consistently, rather than racing the timer.
        """
        with self._lock:
            self._finished = True
            return self.fired

    def watch(self, sock: socket.socket) -> None:
        """Have the watchdog shut this socket down when the deadline passes."""
        with self._lock:
            if self.fired:
                _shutdown(sock)
                raise FetchTimeoutError("Fetching the URL took too long")
            self._sockets.append(sock)

    def timeout(self) -> float:
        remaining = self._end - time.monotonic()
        if remaining <= 0 or self.fired:
            raise FetchTimeoutError("Fetching the URL took too long")
        return min(FETCH_TIMEOUT, remaining)


def _shutdown(sock: socket.socket) -> None:
    # Call the plain socket method even for an SSLSocket: its own shutdown()
    # tears down the TLS object under the thread that is reading from it.
    # Shutting the fd down (without closing it) wakes any blocked recv/send.
    with contextlib.suppress(OSError):
        socket.socket.shutdown(sock, socket.SHUT_RDWR)


_AddrInfo = tuple[socket.AddressFamily, socket.SocketKind, int, str, tuple[Any, ...]]


def _resolve(host: str, port: int, deadline: _Deadline) -> list[_AddrInfo]:
    # getaddrinfo has no timeout of its own, so run it in a daemon thread and
    # stop waiting when the deadline runs out. Failing to resolve is reported
    # the same way as resolving to an internal address, so the error can't be
    # used to probe which internal hostnames exist.
    slots = _lookup_slots
    if not slots.acquire(timeout=deadline.timeout()):
        raise FetchTimeoutError("Fetching the URL took too long")
    result: list[list[_AddrInfo]] = []

    def lookup() -> None:
        try:
            with contextlib.suppress(OSError, UnicodeError):
                result.append(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        finally:
            slots.release()

    thread = threading.Thread(target=lookup, daemon=True)
    try:
        thread.start()
    except BaseException:
        slots.release()
        raise
    thread.join(deadline.timeout())
    if thread.is_alive() or not result or not result[0]:
        raise BlockedURLError(f"Could not resolve host: {host}")
    return result[0]


def _open_socket(host: str, port: int, deadline: _Deadline, allow_private: bool) -> socket.socket:
    infos = _resolve(host, port, deadline)
    if not allow_private:
        for info in infos:
            if not is_public_address(str(info[4][0])):
                raise BlockedURLError(f"Host resolves to a non-public address: {host}")

    last_error: OSError = OSError(f"Could not connect to {host}")
    for family, socktype, proto, _, sockaddr in infos:
        sock = socket.socket(family, socktype, proto)
        try:
            sock.settimeout(deadline.timeout())
            sock.connect(sockaddr)
            deadline.watch(sock)
            return sock
        except OSError as exc:
            sock.close()
            last_error = exc
        except BaseException:
            sock.close()
            raise
    raise last_error


class _HTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, *, deadline: _Deadline, allow_private: bool, **kwargs: Any) -> None:
        super().__init__(host, **kwargs)
        self._deadline = deadline
        self._allow_private = allow_private

    def connect(self) -> None:
        self.sock = _open_socket(self.host, self.port, self._deadline, self._allow_private)


class _HTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, *, deadline: _Deadline, allow_private: bool, **kwargs: Any) -> None:
        super().__init__(host, context=_SSL_CONTEXT, **kwargs)
        self._deadline = deadline
        self._allow_private = allow_private

    def connect(self) -> None:
        sock = _open_socket(self.host, self.port, self._deadline, self._allow_private)
        try:
            # wrap_socket detaches the plain socket, so hand the watchdog the TLS
            # socket before the handshake starts.
            tls_sock = _SSL_CONTEXT.wrap_socket(sock, server_hostname=self.host, do_handshake_on_connect=False)
        except BaseException:
            sock.close()
            raise
        try:
            self._deadline.watch(tls_sock)
            tls_sock.do_handshake()
        except BaseException:
            tls_sock.close()
            raise
        self.sock = tls_sock


class _HTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, deadline: _Deadline, allow_private: bool) -> None:
        super().__init__()
        self._deadline = deadline
        self._allow_private = allow_private

    def http_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        def make(host: str, **kwargs: Any) -> _HTTPConnection:
            return _HTTPConnection(host, deadline=self._deadline, allow_private=self._allow_private, **kwargs)

        return self.do_open(make, req)  # pyright: ignore[reportArgumentType]


class _HTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, deadline: _Deadline, allow_private: bool) -> None:
        super().__init__()
        self._deadline = deadline
        self._allow_private = allow_private

    def https_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        def make(host: str, **kwargs: Any) -> _HTTPSConnection:
            return _HTTPSConnection(host, deadline=self._deadline, allow_private=self._allow_private, **kwargs)

        return self.do_open(make, req)  # pyright: ignore[reportArgumentType]


class _RedirectHandler(urllib.request.HTTPRedirectHandler):
    def http_error_302(self, req: urllib.request.Request, fp: Any, code: int, msg: str, headers: Any) -> Any:
        # The stdlib rejects most foreign schemes itself but lets ftp through
        # (and reports the rest as an HTTP error), so check the target first.
        location = headers.get("location") or headers.get("uri")
        if location:
            target = urllib.parse.urljoin(req.full_url, location)
            if urllib.parse.urlsplit(target).scheme.lower() not in ALLOWED_SCHEMES:
                fp.close()
                raise BlockedURLError("Redirect to a non-http(s) URL")
        return super().http_error_302(req, fp, code, msg, headers)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


def _build_opener(deadline: _Deadline, allow_private: bool) -> urllib.request.OpenerDirector:
    # Built by hand rather than with build_opener() so that no file/ftp/data
    # handlers and no environment proxy handler are registered.
    opener = urllib.request.OpenerDirector()
    for handler in (
        _HTTPHandler(deadline, allow_private),
        _HTTPSHandler(deadline, allow_private),
        _RedirectHandler(),
        urllib.request.HTTPDefaultErrorHandler(),
        urllib.request.HTTPErrorProcessor(),
    ):
        opener.add_handler(handler)
    # The default "Python-urllib/x.y" is blocked outright by many CDNs.
    opener.addheaders = [("User-Agent", USER_AGENT)]
    return opener


def _download(url: str, dest_path: str, max_bytes: int, deadline: _Deadline, allow_private: bool) -> None:
    with _build_opener(deadline, allow_private).open(url, timeout=FETCH_TIMEOUT) as response:
        content_length = response.headers.get("Content-Length", "")
        if content_length.isdigit() and int(content_length) > max_bytes:
            raise TooLargeError("Remote file is too large")
        with open(dest_path, "wb") as f:
            total = 0
            while chunk := response.read(64 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise TooLargeError("Remote file is too large")
                f.write(chunk)


def fetch_url_to_file(url: object, dest_path: str, max_bytes: int, *, allow_private: bool = False) -> None:
    """Download an http(s) URL to dest_path.

    Unless allow_private is set, only hosts that resolve exclusively to public
    addresses are fetched. Raises BlockedURLError if the URL is not allowed,
    FetchError for any other failure. On failure dest_path may hold a partial
    download; the caller owns cleaning it up.
    """
    if not isinstance(url, str):
        raise BlockedURLError("URL must be a string")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES or not parsed.hostname:
        raise BlockedURLError("Only http and https URLs are allowed")

    with _Deadline(FETCH_DEADLINE) as deadline:
        try:
            _download(url, dest_path, max_bytes, deadline, allow_private)
        except FetchError:
            raise
        except Exception as exc:
            if deadline.fired:
                # The watchdog cut the connection; whatever broke is fallout.
                raise FetchTimeoutError("Fetching the URL took too long") from exc
            raise _as_fetch_error(exc) from exc
        if deadline.finish():
            # A shut-down socket reads as EOF, which ends a body of unknown length.
            raise FetchTimeoutError("Fetching the URL took too long")


def _as_fetch_error(exc: Exception) -> FetchError:
    if isinstance(exc, urllib.error.HTTPError):
        exc.close()
        return FetchError(f"Remote server returned HTTP {exc.code}")
    if isinstance(exc, urllib.error.URLError):
        if isinstance(exc.reason, TimeoutError):
            return FetchTimeoutError("Timed out fetching the URL")
        return FetchError("Could not connect to the remote server")
    if isinstance(exc, TimeoutError):
        return FetchTimeoutError("Timed out fetching the URL")
    return FetchError("Could not fetch the URL")
