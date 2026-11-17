import http.server
import os
import socket
import tempfile
import threading
import time
from collections.abc import Iterator
from typing import ClassVar

import pytest
import url_fetch


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.0.0.5",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "224.0.0.1",
        "::1",
        "fe80::1",
        "fd00:ec2::254",
        "::ffff:127.0.0.1",
        "64:ff9b::a9fe:a9fe",
        "2002:7f00:1::1",
        "2002:808:808::1",
        "192.0.0.8",
        "198.18.0.1",
        "240.0.0.1",
        "255.255.255.255",
        "2001:db8::1",
        "ff02::1",
        "64:ff9b:1::1",
        "2001:0:4136:e378:8000:63bf:80ff:fffe",
        "2001:0:4136:e378:8000:63bf:f7f7:f7f7",
    ],
)
def test_non_public_addresses_rejected(ip: str) -> None:
    assert not url_fetch.is_public_address(ip)


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111", "::ffff:8.8.8.8", "64:ff9b::808:808"])
def test_public_addresses_allowed(ip: str) -> None:
    assert url_fetch.is_public_address(ip)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/hostname",
        "ftp://example.com/x.png",
        "data:image/png;base64,AAAA",
        "gopher://example.com/",
        "http://",
        "/etc/passwd",
        123,
        None,
    ],
)
@pytest.mark.parametrize("allow_private", [False, True])
def test_disallowed_urls_rejected(url: object, allow_private: bool) -> None:
    with tempfile.TemporaryDirectory() as d, pytest.raises(url_fetch.BlockedURLError):
        url_fetch.fetch_url_to_file(url, os.path.join(d, "out"), 1024, allow_private=allow_private)


class _Handler(http.server.BaseHTTPRequestHandler):
    hits: ClassVar[list[str]] = []
    user_agents: ClassVar[list[str]] = []

    def do_GET(self) -> None:  # noqa: N802
        _Handler.hits.append(self.path)
        _Handler.user_agents.append(self.headers.get("User-Agent", ""))
        if self.path == "/slow":
            self.send_response(200)
            self.end_headers()
            for _ in range(50):
                self.wfile.write(b"x")
                self.wfile.flush()
                time.sleep(0.1)
            return
        if self.path == "/slow-headers":
            self.send_response(200)
            for _ in range(50):
                self.send_header("X-Pad", "x")
                self.flush_headers()
                time.sleep(0.1)
            self.end_headers()
            self.wfile.write(b"late")
            return
        if self.path == "/big":
            self.send_response(200)
            self.send_header("Content-Length", "1000000")
            self.end_headers()
            return
        if self.path == "/missing":
            self.send_response(404)
            self.end_headers()
            return
        if self.path == "/to-file":
            self.send_response(302)
            self.send_header("Location", "file:///etc/hostname")
            self.end_headers()
            return
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_address[1]}/secret")
            self.end_headers()
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"secret")

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def loopback_server() -> Iterator[int]:
    _Handler.hits = []
    _Handler.user_agents = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


@pytest.fixture
def silent_server() -> Iterator[int]:
    """Accepts connections and never sends a byte (a stalled TLS handshake)."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    accepted: list[socket.socket] = []
    stop = threading.Event()

    def accept() -> None:
        server.settimeout(0.1)
        while not stop.is_set():
            try:
                accepted.append(server.accept()[0])
            except TimeoutError:
                continue

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    yield server.getsockname()[1]
    stop.set()
    thread.join()
    for conn in accepted:
        conn.close()
    server.close()


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "[::ffff:127.0.0.1]"])
def test_loopback_service_never_contacted(loopback_server: int, host: str) -> None:
    with tempfile.TemporaryDirectory() as d, pytest.raises(url_fetch.BlockedURLError):
        url_fetch.fetch_url_to_file(f"http://{host}:{loopback_server}/latest/meta-data/", os.path.join(d, "out"), 1024)
    assert _Handler.hits == []


def test_allow_private_fetches_internal_hosts(loopback_server: int) -> None:
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "out")
        url_fetch.fetch_url_to_file(f"http://127.0.0.1:{loopback_server}/ok", out, 1024, allow_private=True)
        with open(out, "rb") as f:
            assert f.read() == b"secret"


def test_redirect_to_internal_address_blocked(loopback_server: int, monkeypatch: pytest.MonkeyPatch) -> None:
    # Allow the first hop to the test server, then make sure the redirect hop is re-checked.
    real_check = url_fetch.is_public_address
    calls: list[str] = []

    def allow_first(ip: str) -> bool:
        calls.append(ip)
        return len(calls) == 1 or real_check(ip)

    monkeypatch.setattr(url_fetch, "is_public_address", allow_first)
    with tempfile.TemporaryDirectory() as d, pytest.raises(url_fetch.BlockedURLError):
        url_fetch.fetch_url_to_file(f"http://127.0.0.1:{loopback_server}/redirect", os.path.join(d, "out"), 1024)
    assert _Handler.hits == ["/redirect"]


def test_size_limit_enforced(loopback_server: int) -> None:
    with tempfile.TemporaryDirectory() as d, pytest.raises(url_fetch.FetchError, match="too large"):
        url_fetch.fetch_url_to_file(
            f"http://127.0.0.1:{loopback_server}/ok", os.path.join(d, "out"), 3, allow_private=True
        )


def test_content_length_over_limit_rejected_before_download(loopback_server: int) -> None:
    with tempfile.TemporaryDirectory() as d, pytest.raises(url_fetch.FetchError, match="too large"):
        url_fetch.fetch_url_to_file(
            f"http://127.0.0.1:{loopback_server}/big", os.path.join(d, "out"), 1024, allow_private=True
        )


@pytest.mark.parametrize("path", ["/slow", "/slow-headers"])
def test_trickling_server_hits_overall_deadline(
    loopback_server: int, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    # Each byte arrives well inside the per-recv timeout; only the overall
    # deadline can stop it, whether it trickles the body or the headers.
    monkeypatch.setattr(url_fetch, "FETCH_DEADLINE", 1)
    start = time.monotonic()
    with tempfile.TemporaryDirectory() as d, pytest.raises(url_fetch.FetchError, match="too long"):
        url_fetch.fetch_url_to_file(
            f"http://127.0.0.1:{loopback_server}{path}", os.path.join(d, "out"), 1024, allow_private=True
        )
    assert time.monotonic() - start < 2


def test_stalled_tls_handshake_hits_overall_deadline(silent_server: int, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(url_fetch, "FETCH_DEADLINE", 1)
    start = time.monotonic()
    with tempfile.TemporaryDirectory() as d, pytest.raises(url_fetch.FetchError, match="too long"):
        url_fetch.fetch_url_to_file(
            f"https://127.0.0.1:{silent_server}/", os.path.join(d, "out"), 1024, allow_private=True
        )
    assert time.monotonic() - start < 2


def test_dns_lookup_bounded_by_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    release = threading.Event()

    def hanging_getaddrinfo(*args: object, **kwargs: object) -> list[object]:
        release.wait(5)
        return []

    monkeypatch.setattr(url_fetch.socket, "getaddrinfo", hanging_getaddrinfo)
    monkeypatch.setattr(url_fetch, "FETCH_DEADLINE", 1)
    start = time.monotonic()
    try:
        with tempfile.TemporaryDirectory() as d, pytest.raises(url_fetch.BlockedURLError):
            url_fetch.fetch_url_to_file("http://example.invalid/x.png", os.path.join(d, "out"), 1024)
        assert time.monotonic() - start < 2
    finally:
        release.set()


def test_unresolvable_host_looks_like_a_blocked_one(monkeypatch: pytest.MonkeyPatch) -> None:
    # Telling "doesn't resolve" apart from "resolves to an internal address"
    # would let callers enumerate internal hostnames.
    def nxdomain(*args: object, **kwargs: object) -> list[object]:
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    monkeypatch.setattr(url_fetch.socket, "getaddrinfo", nxdomain)
    with tempfile.TemporaryDirectory() as d, pytest.raises(url_fetch.BlockedURLError):
        url_fetch.fetch_url_to_file("http://does-not-exist.invalid/x.png", os.path.join(d, "out"), 1024)


def test_pending_lookups_are_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    release = threading.Event()
    started = threading.Semaphore(0)

    def hanging_getaddrinfo(*args: object, **kwargs: object) -> list[object]:
        started.release()
        release.wait(5)
        return []

    monkeypatch.setattr(url_fetch.socket, "getaddrinfo", hanging_getaddrinfo)
    monkeypatch.setattr(url_fetch, "_lookup_slots", threading.BoundedSemaphore(1))
    monkeypatch.setattr(url_fetch, "FETCH_DEADLINE", 0.5)
    try:
        with tempfile.TemporaryDirectory() as d:
            for _ in range(2):
                with pytest.raises(url_fetch.FetchError):
                    url_fetch.fetch_url_to_file("http://example.invalid/x.png", os.path.join(d, "out"), 1024)
        # The first lookup is still hanging, so the second never started one.
        assert started.acquire(timeout=0)
        assert not started.acquire(timeout=0)
    finally:
        release.set()


def test_http_error_is_fetch_error(loopback_server: int) -> None:
    with tempfile.TemporaryDirectory() as d, pytest.raises(url_fetch.FetchError, match="HTTP 404") as exc_info:
        url_fetch.fetch_url_to_file(
            f"http://127.0.0.1:{loopback_server}/missing", os.path.join(d, "out"), 1024, allow_private=True
        )
    assert not isinstance(exc_info.value, url_fetch.BlockedURLError)


def test_unreachable_host_is_fetch_error() -> None:
    with tempfile.TemporaryDirectory() as d, pytest.raises(url_fetch.FetchError) as exc_info:
        url_fetch.fetch_url_to_file("http://127.0.0.1:1/", os.path.join(d, "out"), 1024, allow_private=True)
    assert not isinstance(exc_info.value, url_fetch.BlockedURLError)


def test_redirect_to_non_http_scheme_blocked(loopback_server: int) -> None:
    with tempfile.TemporaryDirectory() as d, pytest.raises(url_fetch.BlockedURLError):
        url_fetch.fetch_url_to_file(
            f"http://127.0.0.1:{loopback_server}/to-file", os.path.join(d, "out"), 1024, allow_private=True
        )


def test_requests_carry_imgpush_user_agent(loopback_server: int) -> None:
    with tempfile.TemporaryDirectory() as d:
        url_fetch.fetch_url_to_file(
            f"http://127.0.0.1:{loopback_server}/x.png", os.path.join(d, "out"), 1024, allow_private=True
        )
    assert _Handler.user_agents == [url_fetch.USER_AGENT]


def test_deadline_firing_after_finish_is_ignored() -> None:
    # A fetch that completed just before the timer went off must not be
    # reported as having taken too long.
    deadline = url_fetch._Deadline(60)
    assert deadline.finish() is False
    deadline._fire()
    assert deadline.fired is False


def test_deadline_fired_before_finish_is_reported() -> None:
    deadline = url_fetch._Deadline(60)
    deadline._fire()
    assert deadline.finish() is True
