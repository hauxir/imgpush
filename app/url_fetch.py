"""Fetch user-supplied URLs without allowing SSRF.

Only http/https are allowed, and every connection (including each redirect hop)
resolves the host, rejects any non-public address, and connects to exactly the
address that was checked, so DNS rebinding cannot swap in an internal IP.

The whole fetch (DNS, connects, TLS, redirects and body) runs against one
wall-clock deadline: DNS lookups are bounded and every socket read gets a
timeout no longer than the time left.
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
from typing import Any, cast

ALLOWED_SCHEMES = ("http", "https")
# Upper bound for any single connect/read.
FETCH_TIMEOUT = 10
# Wall-clock budget for the whole fetch: DNS, connects, redirects and body.
FETCH_DEADLINE = 30

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
_GLOBAL_UNICAST_V6 = ipaddress.IPv6Network("2000::/3")
_BLOCKED_V6 = tuple(
    ipaddress.IPv6Network(n)
    for n in (
        "2001::/23",  # IETF protocol assignments (incl. Teredo, handled above)
        "2001:db8::/32",  # documentation
        "2002::/16",  # 6to4 (handled above)
        "3fff::/20",  # documentation
    )
)
_NAT64_PREFIX = ipaddress.IPv6Network("64:ff9b::/96")


class FetchError(Exception):
    """The URL could not be fetched. The message is safe to show to the user."""


class BlockedURLError(FetchError):
    """The URL is not allowed to be fetched at all."""


def _is_public_v4(addr: ipaddress.IPv4Address) -> bool:
    return not any(addr in net for net in _BLOCKED_V4)


def is_public_address(ip: str) -> bool:
    addr = ipaddress.ip_address(ip.split("%", 1)[0])
    if isinstance(addr, ipaddress.IPv4Address):
        return _is_public_v4(addr)
    # IPv6 forms that carry an IPv4 address are judged by that address.
    embedded: ipaddress.IPv4Address | None = addr.ipv4_mapped or addr.sixtofour
    if embedded is None and addr.teredo:
        embedded = addr.teredo[1]
    if embedded is None and addr in _NAT64_PREFIX:
        embedded = ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)
    if embedded is not None:
        return _is_public_v4(embedded)
    return addr in _GLOBAL_UNICAST_V6 and not any(addr in net for net in _BLOCKED_V6)


class _Deadline:
    def __init__(self, seconds: float) -> None:
        self._end = time.monotonic() + seconds
        # The socket the response body is currently being read from.
        self.sock: socket.socket | None = None

    def remaining(self) -> float:
        remaining = self._end - time.monotonic()
        if remaining <= 0:
            raise FetchError("Fetching the URL took too long")
        return remaining

    def expired(self) -> bool:
        return time.monotonic() >= self._end

    def timeout(self) -> float:
        return min(FETCH_TIMEOUT, self.remaining())


_AddrInfo = tuple[socket.AddressFamily, socket.SocketKind, int, str, tuple[Any, ...]]


def _resolve(host: str, port: int, deadline: _Deadline) -> list[_AddrInfo]:
    # getaddrinfo has no timeout of its own, so run it in a daemon thread and
    # stop waiting when the deadline runs out.
    result: list[list[_AddrInfo]] = []

    def lookup() -> None:
        with contextlib.suppress(OSError):
            result.append(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))

    thread = threading.Thread(target=lookup, daemon=True)
    thread.start()
    thread.join(deadline.timeout())
    if thread.is_alive():
        raise FetchError(f"Timed out resolving host: {host}")
    if not result or not result[0]:
        raise FetchError(f"Could not resolve host: {host}")
    return result[0]


def _open_public_socket(host: str, port: int, deadline: _Deadline) -> socket.socket:
    infos = _resolve(host, port, deadline)
    for info in infos:
        if not is_public_address(str(info[4][0])):
            raise BlockedURLError(f"Host resolves to a non-public address: {host}")

    last_error: OSError = OSError(f"Could not connect to {host}")
    for family, socktype, proto, _, sockaddr in infos:
        sock = socket.socket(family, socktype, proto)
        try:
            sock.settimeout(deadline.timeout())
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            sock.close()
            last_error = exc
        except BaseException:
            sock.close()
            raise
    raise last_error


class _PublicHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, *, deadline: _Deadline, **kwargs: Any) -> None:
        super().__init__(host, **kwargs)
        self._deadline = deadline

    def connect(self) -> None:
        self.sock = _open_public_socket(self.host, self.port, self._deadline)
        self._deadline.sock = self.sock


class _PublicHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, *, deadline: _Deadline, **kwargs: Any) -> None:
        super().__init__(host, **kwargs)
        self._deadline = deadline

    def connect(self) -> None:
        sock = _open_public_socket(self.host, self.port, self._deadline)
        try:
            # Same context HTTPSConnection.connect would use (the handler's, or the default).
            context = cast(ssl.SSLContext, getattr(self, "_context"))  # noqa: B009 - private, untyped in typeshed
            tls_sock = context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise
        self.sock = tls_sock
        self._deadline.sock = tls_sock


class _PublicHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, deadline: _Deadline) -> None:
        super().__init__()
        self._deadline = deadline

    def http_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        def make(host: str, **kwargs: Any) -> _PublicHTTPConnection:
            return _PublicHTTPConnection(host, deadline=self._deadline, **kwargs)

        return self.do_open(make, req)  # pyright: ignore[reportArgumentType]


class _PublicHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, deadline: _Deadline) -> None:
        super().__init__()
        self._deadline = deadline

    def https_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        def make(host: str, **kwargs: Any) -> _PublicHTTPSConnection:
            return _PublicHTTPSConnection(host, deadline=self._deadline, **kwargs)

        return self.do_open(make, req)  # pyright: ignore[reportArgumentType]


class _PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
    def http_error_302(self, req: urllib.request.Request, fp: Any, code: int, msg: str, headers: Any) -> Any:
        # The stdlib rejects most foreign schemes itself but lets ftp through
        # (and reports the rest as an HTTP error), so check the target first.
        location = headers.get("location") or headers.get("uri")
        if location:
            target = urllib.parse.urljoin(req.full_url, location)
            if urllib.parse.urlsplit(target).scheme.lower() not in ALLOWED_SCHEMES:
                raise BlockedURLError("Redirect to a non-http(s) URL")
        return super().http_error_302(req, fp, code, msg, headers)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


def _build_opener(deadline: _Deadline) -> urllib.request.OpenerDirector:
    # Built by hand rather than with build_opener() so that no file/ftp/data
    # handlers and no environment proxy handler are registered.
    opener = urllib.request.OpenerDirector()
    for handler in (
        _PublicHTTPHandler(deadline),
        _PublicHTTPSHandler(deadline),
        _PublicRedirectHandler(),
        urllib.request.HTTPDefaultErrorHandler(),
        urllib.request.HTTPErrorProcessor(),
    ):
        opener.add_handler(handler)
    return opener


def _download(url: str, dest_path: str, max_bytes: int, deadline: _Deadline) -> None:
    with _build_opener(deadline).open(url, timeout=FETCH_TIMEOUT) as response:
        content_length = response.headers.get("Content-Length", "")
        if content_length.isdigit() and int(content_length) > max_bytes:
            raise FetchError("Remote file is too large")
        with open(dest_path, "wb") as f:
            total = 0
            while True:
                # Re-arm the socket timeout before every read so that no single
                # read can outlast the overall deadline.
                if deadline.sock is not None:
                    deadline.sock.settimeout(deadline.timeout())
                # read1 returns after a single recv, so a server trickling bytes
                # cannot keep one read call alive past the deadline check.
                chunk = response.read1(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise FetchError("Remote file is too large")
                f.write(chunk)


def _timeout_error(deadline: _Deadline) -> FetchError:
    if deadline.expired():
        return FetchError("Fetching the URL took too long")
    return FetchError("Timed out fetching the URL")


def fetch_url_to_file(url: object, dest_path: str, max_bytes: int) -> None:
    """Download a public http(s) URL to dest_path.

    Raises BlockedURLError if the URL is not allowed, FetchError for any other
    failure. On failure dest_path may hold a partial download; the caller owns
    cleaning it up.
    """
    if not isinstance(url, str):
        raise BlockedURLError("URL must be a string")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES or not parsed.hostname:
        raise BlockedURLError("Only http and https URLs are allowed")

    deadline = _Deadline(FETCH_DEADLINE)
    try:
        _download(url, dest_path, max_bytes, deadline)
    except FetchError:
        raise
    except urllib.error.HTTPError as exc:
        exc.close()
        raise FetchError(f"Remote server returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise _timeout_error(deadline) from exc
        raise FetchError("Could not connect to the remote server") from exc
    except TimeoutError as exc:
        raise _timeout_error(deadline) from exc
    except (OSError, http.client.HTTPException, ValueError) as exc:
        raise FetchError("Could not fetch the URL") from exc
