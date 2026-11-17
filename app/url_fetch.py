"""Fetch user-supplied URLs without allowing SSRF.

Only http/https are allowed, and every connection (including each redirect hop)
resolves the host, rejects any non-public address, and connects to exactly the
address that was checked, so DNS rebinding cannot swap in an internal IP.
"""

import http.client
import ipaddress
import socket
import ssl
import threading
import time
import urllib.parse
import urllib.request
from typing import Any

ALLOWED_SCHEMES = ("http", "https")
FETCH_TIMEOUT = 10
# Wall-clock budget for the whole fetch: DNS, connects, redirects and body.
FETCH_DEADLINE = 30
_NAT64_PREFIX = ipaddress.IPv6Network("64:ff9b::/96")


class BlockedURLError(Exception):
    pass


_state = threading.local()


def _remaining() -> float:
    remaining = getattr(_state, "deadline", time.monotonic() + FETCH_DEADLINE) - time.monotonic()
    if remaining <= 0:
        raise BlockedURLError("Fetching the URL took too long")
    return remaining


def is_public_address(ip: str) -> bool:
    addr = ipaddress.ip_address(ip.split("%", 1)[0])
    if isinstance(addr, ipaddress.IPv6Address):
        embedded: ipaddress.IPv4Address | None = addr.ipv4_mapped or addr.sixtofour
        if embedded is None and addr.teredo:
            embedded = addr.teredo[1]
        if embedded is None and addr in _NAT64_PREFIX:
            embedded = ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)
        if embedded is not None:
            return embedded.is_global and not embedded.is_multicast
    return addr.is_global and not addr.is_multicast


def _open_public_socket(host: str, port: int, timeout: Any) -> socket.socket:
    _remaining()
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise BlockedURLError(f"Could not resolve host: {host}") from exc
    if not infos:
        raise BlockedURLError(f"Could not resolve host: {host}")
    for info in infos:
        if not is_public_address(str(info[4][0])):
            raise BlockedURLError(f"Host resolves to a non-public address: {host}")

    last_error: OSError | None = None
    for family, socktype, proto, _, sockaddr in infos:
        sock = socket.socket(family, socktype, proto)
        try:
            base = timeout if isinstance(timeout, (int, float)) else FETCH_TIMEOUT
            sock.settimeout(min(base, _remaining()))
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            sock.close()
            last_error = exc
    assert last_error is not None
    raise last_error


class _PublicHTTPConnection(http.client.HTTPConnection):
    def connect(self) -> None:
        self.sock = _open_public_socket(self.host, self.port, self.timeout)


class _PublicHTTPSConnection(http.client.HTTPSConnection):
    def connect(self) -> None:
        sock = _open_public_socket(self.host, self.port, self.timeout)
        self.sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self.host)


class _PublicHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        return self.do_open(_PublicHTTPConnection, req)


class _PublicHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        return self.do_open(_PublicHTTPSConnection, req)


def _build_opener() -> urllib.request.OpenerDirector:
    # Built by hand rather than with build_opener() so that no file/ftp/data
    # handlers and no environment proxy handler are registered.
    opener = urllib.request.OpenerDirector()
    for handler in (
        _PublicHTTPHandler(),
        _PublicHTTPSHandler(),
        urllib.request.HTTPRedirectHandler(),
        urllib.request.HTTPDefaultErrorHandler(),
        urllib.request.HTTPErrorProcessor(),
    ):
        opener.add_handler(handler)
    return opener


def fetch_url_to_file(url: object, dest_path: str, max_bytes: int) -> None:
    if not isinstance(url, str):
        raise BlockedURLError("URL must be a string")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES or not parsed.hostname:
        raise BlockedURLError("Only http and https URLs are allowed")

    _state.deadline = time.monotonic() + FETCH_DEADLINE
    try:
        with _build_opener().open(url, timeout=FETCH_TIMEOUT) as response, open(dest_path, "wb") as f:
            total = 0
            # read1 returns after a single recv, so a server trickling bytes
            # cannot keep one read call alive past the deadline check.
            while chunk := response.read1(64 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise BlockedURLError("Remote file is too large")
                f.write(chunk)
                _remaining()
    finally:
        del _state.deadline
