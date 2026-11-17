import http.server
import os
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
    ],
)
def test_non_public_addresses_rejected(ip: str) -> None:
    assert not url_fetch.is_public_address(ip)


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"])
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
def test_disallowed_urls_rejected(url: object) -> None:
    with tempfile.TemporaryDirectory() as d, pytest.raises(url_fetch.BlockedURLError):
        url_fetch.fetch_url_to_file(url, os.path.join(d, "out"), 1024)


class _Handler(http.server.BaseHTTPRequestHandler):
    hits: ClassVar[list[str]] = []

    def do_GET(self) -> None:  # noqa: N802
        _Handler.hits.append(self.path)
        if self.path == "/slow":
            self.send_response(200)
            self.end_headers()
            for _ in range(50):
                self.wfile.write(b"x")
                self.wfile.flush()
                time.sleep(0.1)
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
    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "[::ffff:127.0.0.1]"])
def test_loopback_service_never_contacted(loopback_server: int, host: str) -> None:
    with tempfile.TemporaryDirectory() as d, pytest.raises(url_fetch.BlockedURLError):
        url_fetch.fetch_url_to_file(f"http://{host}:{loopback_server}/latest/meta-data/", os.path.join(d, "out"), 1024)
    assert _Handler.hits == []


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


def test_size_limit_enforced(loopback_server: int, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(url_fetch, "is_public_address", lambda ip: True)
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "out")
        url_fetch.fetch_url_to_file(f"http://127.0.0.1:{loopback_server}/ok", out, 1024)
        with open(out, "rb") as f:
            assert f.read() == b"secret"
        with pytest.raises(url_fetch.BlockedURLError):
            url_fetch.fetch_url_to_file(f"http://127.0.0.1:{loopback_server}/ok", out, 3)


def test_trickling_server_hits_overall_deadline(loopback_server: int, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(url_fetch, "is_public_address", lambda ip: True)
    monkeypatch.setattr(url_fetch, "FETCH_DEADLINE", 1)
    start = time.monotonic()
    with tempfile.TemporaryDirectory() as d, pytest.raises(url_fetch.BlockedURLError):
        url_fetch.fetch_url_to_file(f"http://127.0.0.1:{loopback_server}/slow", os.path.join(d, "out"), 1024)
    assert time.monotonic() - start < 3
