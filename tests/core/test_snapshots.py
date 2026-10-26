import io
import socket
import ssl
from contextlib import contextmanager

import pytest

from xyle_trace.core import snapshots
from xyle_trace.core.hashing import hash_bytes, hash_file
from xyle_trace.core.snapshots import CaptureError, CaptureLimits, SnapshotCapture


class Response(io.BytesIO):
    def __init__(self, body=b"observations", *, status=200, headers=None):
        super().__init__(body)
        self.status = status
        self.headers = headers or {}

    def getheader(self, name, default=None):
        return self.headers.get(name, default)


class Transport:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []

    @contextmanager
    def __call__(self, url, timeout):
        self.calls.append((url, timeout))
        with next(self.responses) as response:
            yield response


def blobs(root):
    return list((root / ".lineage" / "snapshots").glob("*"))


def test_local_capture_versions_bytes_and_reuses_blobs(tmp_path):
    path = tmp_path / "input"
    path.write_bytes(b"original")
    capture = SnapshotCapture(tmp_path)
    first = capture.local("input")
    path.rename(tmp_path / "moved")
    retry = capture.local("moved")
    assert first.path == retry.path
    assert first.content_hash == hash_file(tmp_path / first.path) == hash_bytes(b"original")
    assert first.size_bytes == 8
    assert len(blobs(tmp_path)) == 1
    (tmp_path / "moved").write_bytes(b"new")
    changed = capture.local("moved")
    assert changed.content_hash != first.content_hash
    assert (tmp_path / first.path).read_bytes() == b"original"
    assert len(blobs(tmp_path)) == 2


@pytest.mark.parametrize("path", ["missing", "../outside", "/etc/hosts", "."])
def test_invalid_local_input_leaves_no_blob(tmp_path, path):
    with pytest.raises(CaptureError):
        SnapshotCapture(tmp_path).local(path)
    assert blobs(tmp_path) == []


def test_symlinks_resolve_inside_root_and_cannot_escape(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (tmp_path / "outside").write_bytes(b"outside")
    (root / "input").symlink_to(tmp_path / "outside")
    capture = SnapshotCapture(root)
    with pytest.raises(CaptureError, match="escapes"):
        capture.local("input")
    (root / "input").unlink()
    (root / "actual").write_bytes(b"inside")
    (root / "input").symlink_to(root / "actual")
    result = capture.local("input")
    assert result.retrieval == {"kind": "local", "path": "actual"}


@pytest.mark.parametrize("component", [".lineage", ".lineage/snapshots"])
def test_managed_storage_cannot_be_redirected(tmp_path, component):
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    target = root / component
    target.parent.mkdir(exist_ok=True)
    target.symlink_to(outside, target_is_directory=True)
    (root / "input").write_bytes(b"data")
    with pytest.raises(CaptureError, match="symlinks"):
        SnapshotCapture(root).local("input")
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("corruption", ["bytes", "symlink", "directory"])
def test_corrupt_blob_is_never_replaced(tmp_path, corruption):
    (tmp_path / "input").write_bytes(b"data")
    capture = SnapshotCapture(tmp_path)
    result = capture.local("input")
    path = tmp_path / result.path
    path.unlink()
    if corruption == "bytes":
        path.write_bytes(b"corrupt")
    elif corruption == "symlink":
        path.symlink_to(tmp_path / "input")
    else:
        path.mkdir()
    with pytest.raises(CaptureError, match="corrupt or unsafe"):
        capture.local("input")
    assert len(blobs(tmp_path)) == 1
    if corruption == "bytes":
        assert path.read_bytes() == b"corrupt"
    elif corruption == "symlink":
        assert path.is_symlink()
    else:
        assert path.is_dir()


def test_http_redirect_captures_final_url_and_closes_every_response(tmp_path):
    redirect = Response(status=302, headers={"Location": "/final#section"})
    final = Response(b"data", headers={"Content-Length": "4"})
    transport = Transport(redirect, final)
    result = SnapshotCapture(tmp_path, transport=transport).http("https://example.test/start")
    assert transport.calls == [
        ("https://example.test/start", 30),
        ("https://example.test/final", 30),
    ]
    assert redirect.closed and final.closed
    assert result.retrieval == {"kind": "http", "final_url": "https://example.test/final"}
    assert result.content_hash == hash_bytes(b"data")
    assert (tmp_path / result.path).read_bytes() == b"data"


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/hosts",
        "ftp://example.test/data",
        "https://user:secret@example.test",
        "http://127.0.0.1",
        "http://10.0.0.1",
        "http://169.254.169.254",
        "http://100.64.0.1",
        "http://[::1]",
        "http://[::ffff:127.0.0.1]",
        "http://224.0.0.1",
        "http://[ff02::1]",
        "http://[fe80::1%25en0]",
        "https://example.test:0",
        "https://example.test:bad",
        "https://example.test/\r\nheader",
    ],
)
def test_unsafe_urls_and_redirects_never_reach_transport(tmp_path, url):
    transport = Transport()
    with pytest.raises(CaptureError):
        SnapshotCapture(tmp_path, transport=transport).http(url)
    assert transport.calls == []
    redirect = Transport(Response(status=302, headers={"Location": url}))
    with pytest.raises(CaptureError):
        SnapshotCapture(tmp_path, transport=redirect).http("https://example.test")
    assert len(redirect.calls) == 1
    assert blobs(tmp_path) == []


@pytest.mark.parametrize(
    "response",
    [
        Response(status=404),
        Response(status=206),
        Response(status=302),
        Response(headers={"Content-Length": "100"}),
        Response(headers={"Content-Length": "-1"}),
        Response(headers={"Content-Length": "1, 1"}),
        Response(headers={"Content-Encoding": "gzip"}),
    ],
)
def test_failed_http_response_leaves_no_blob(tmp_path, response):
    with pytest.raises(CaptureError):
        SnapshotCapture(tmp_path, transport=Transport(response)).http("https://example.test")
    assert response.closed
    assert blobs(tmp_path) == []


def test_redirect_limit_and_custom_timeout(tmp_path):
    transport = Transport(*(Response(status=301, headers={"Location": "/loop"}) for _ in range(3)))
    capture = SnapshotCapture(
        tmp_path, limits=CaptureLimits(max_redirects=2, timeout_seconds=1), transport=transport
    )
    with pytest.raises(CaptureError, match="limit"):
        capture.http("https://example.test")
    assert len(transport.calls) == 3
    assert all(timeout == 1 for _, timeout in transport.calls)


@pytest.mark.parametrize("declared", [True, False])
def test_http_byte_limit_checks_headers_and_stream(tmp_path, declared):
    response = Response(b"12345", headers={"Content-Length": "5"} if declared else {})
    capture = SnapshotCapture(
        tmp_path, limits=CaptureLimits(max_bytes=4), transport=Transport(response)
    )
    with pytest.raises(CaptureError, match="byte limit"):
        capture.http("https://example.test")
    assert blobs(tmp_path) == []


def test_local_byte_limit_and_exact_boundary(tmp_path):
    path = tmp_path / "input"
    path.write_bytes(b"12345")
    capture = SnapshotCapture(tmp_path, limits=CaptureLimits(max_bytes=4))
    with pytest.raises(CaptureError, match="byte limit"):
        capture.local("input")
    assert blobs(tmp_path) == []
    path.write_bytes(b"1234")
    assert capture.local("input").size_bytes == 4
    path.write_bytes(b"")
    assert capture.local("input").content_hash == hash_bytes(b"")


@pytest.mark.parametrize("failure", [TimeoutError, ConnectionResetError, KeyboardInterrupt])
def test_interrupted_stream_cleans_partial_file(tmp_path, failure):
    class Interrupted(Response):
        def read(self, size):
            if self.tell():
                raise failure()
            return super().read(3)

    response = Interrupted()
    capture = SnapshotCapture(tmp_path, transport=Transport(response))
    with pytest.raises(KeyboardInterrupt if failure is KeyboardInterrupt else CaptureError):
        capture.http("https://example.test")
    assert response.closed
    assert blobs(tmp_path) == []


def test_dns_mixed_public_and_private_addresses_are_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 80)) for ip in ("8.8.8.8", "127.0.0.1")
        ],
    )
    monkeypatch.setattr(socket, "socket", lambda *a: pytest.fail("must not connect"))
    with pytest.raises(CaptureError, match="public DNS"):
        SnapshotCapture(tmp_path).http("http://example.test")


@pytest.mark.parametrize("https", [False, True])
def test_default_transport_pins_dns_and_retains_host_and_tls_checks(tmp_path, monkeypatch, https):
    class Socket:
        def __init__(self):
            self.sent = b""
            self.closed = False

        def settimeout(self, timeout):
            assert timeout == 30

        def connect(self, address):
            assert address == ("8.8.8.8", 443 if https else 80)

        def sendall(self, data):
            self.sent += data

        def makefile(self, mode):
            return io.BytesIO(b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\ndata")

        def close(self):
            self.closed = True

    sock = Socket()
    lookups = []

    def resolve(host, port, **kwargs):
        lookups.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(socket, "socket", lambda *args: sock)
    tls_hosts = []

    tls_context = ssl.create_default_context()
    # Prove the transport enforces its own floor even with permissive defaults.
    tls_context.minimum_version = ssl.TLSVersion.MINIMUM_SUPPORTED

    def wrap_socket(context, connection, *, server_hostname):
        assert context.minimum_version >= ssl.TLSVersion.TLSv1_2
        assert context.check_hostname
        assert context.verify_mode == ssl.CERT_REQUIRED
        tls_hosts.append(server_hostname)
        assert connection is sock
        return connection

    monkeypatch.setattr(ssl, "create_default_context", lambda: tls_context)
    monkeypatch.setattr(ssl.SSLContext, "wrap_socket", wrap_socket)
    monkeypatch.setenv("http_proxy", "http://secret:password@127.0.0.1:8888")
    monkeypatch.setenv("https_proxy", "http://secret:password@127.0.0.1:8888")
    result = SnapshotCapture(tmp_path).http(
        f"{'https' if https else 'http'}://example.test/data?q=1"
    )
    assert result.content_hash == hash_bytes(b"data")
    assert lookups == ["example.test"]
    assert tls_hosts == (["example.test"] if https else [])
    assert b"GET /data?q=1 HTTP/1.1\r\n" in sock.sent
    assert b"Host: example.test" in sock.sent
    assert b"Authorization" not in sock.sent and b"Cookie" not in sock.sent
    assert sock.closed


def test_read_snapshot_rejects_oversized_retained_bytes(tmp_path):
    payload = b"12345"
    digest = hash_bytes(payload)
    (tmp_path / digest.removeprefix("sha256:")).write_bytes(payload)
    with pytest.raises(CaptureError, match="byte limit"):
        snapshots.read_snapshot(tmp_path, digest, max_bytes=4)
    assert snapshots.read_snapshot(tmp_path, digest, max_bytes=5) == payload


def test_read_snapshot_rejects_a_fifo_without_waiting_for_a_writer(tmp_path):
    import os

    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO unavailable on this platform")
    digest = hash_bytes(b"fifo")
    os.mkfifo(tmp_path / digest.removeprefix("sha256:"))
    with pytest.raises(CaptureError, match="regular file"):
        snapshots.read_snapshot(tmp_path, digest)
