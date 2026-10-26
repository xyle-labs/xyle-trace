"""Capture opaque source bytes in project-local, content-addressed storage."""

import hashlib
import http.client
import io
import ipaddress
import math
import os
import re
import socket
import ssl
import stat
import tempfile
from collections.abc import Callable
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol
from urllib.parse import urljoin, urlsplit, urlunsplit

from xyle_trace.core.hashing import hash_file

_CHUNK = 1024 * 1024
_REDIRECTS = {301, 302, 303, 307, 308}


class CaptureError(ValueError):
    """A transfer or stored blob could not be verified."""


def read_snapshot(root: Path, content_hash: str, *, max_bytes: int = 25 * 1024 * 1024) -> bytes:
    """Read bounded, regular retained bytes without following managed symlinks.

    Callers verify the hash or report a mismatch. Local storage is trusted;
    this does not isolate against concurrent hostile directory replacement.
    """
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", content_hash):
        raise CaptureError("invalid snapshot hash")
    path = root / content_hash.removeprefix("sha256:")
    if any(item.is_symlink() for item in (root.parent, root, path)):
        raise CaptureError("managed snapshot paths must not be symlinks")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise CaptureError("snapshot must be a regular file")
        if info.st_size > max_bytes:
            raise CaptureError("snapshot exceeds the read byte limit")
        data = stream.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise CaptureError("snapshot exceeds the read byte limit")
        return data


@dataclass(frozen=True)
class CaptureLimits:
    timeout_seconds: float = 30
    max_bytes: int = 25 * 1024 * 1024
    max_redirects: int = 5

    def __post_init__(self):
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("capture timeout must be positive and finite")
        if type(self.max_bytes) is not int or self.max_bytes < 1:
            raise ValueError("capture byte limit must be a positive integer")
        if type(self.max_redirects) is not int or self.max_redirects < 0:
            raise ValueError("capture redirect limit must be a nonnegative integer")


@dataclass(frozen=True)
class CapturedBytes:
    content_hash: str
    size_bytes: int
    path: str
    retrieval: dict[str, str]


class HTTPResponse(Protocol):
    status: int

    def getheader(self, name: str, default=None) -> str | None: ...

    def read(self, size: int) -> bytes: ...


HTTPTransport = Callable[[str, float], AbstractContextManager[HTTPResponse]]


def _public_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return address.is_global and not address.is_multicast and not address.is_reserved


def _url_characters(value: str) -> None:
    # urlsplit strips some controls: reject them before parsing.
    if any(ord(char) <= 32 or ord(char) == 127 for char in value) or "\\" in value:
        raise CaptureError("invalid HTTP URL")


def _url(value: str):
    _url_characters(value)
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise CaptureError("capture requires an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise CaptureError("URL credentials are not allowed")
    if "%" in parsed.hostname or parsed.port == 0:
        raise CaptureError("invalid HTTP address")
    try:
        ipaddress.ip_address(parsed.hostname)
    except ValueError:
        pass  # DNS addresses are checked by the transport before connecting.
    else:
        if not _public_address(parsed.hostname):
            raise CaptureError("HTTP capture requires a public address")
    return parsed


@contextmanager
def public_http(url: str, timeout: float):
    """One GET, pinned to validated DNS results, with verified TLS and no proxies.

    Redirects are handled by SnapshotCapture. A fresh connection for every hop
    prevents forwarding cookies or authentication. Timeout bounds socket operations.
    """
    parsed = _url(url)
    host = parsed.hostname.encode("idna").decode("ascii")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses or any(not _public_address(item[4][0]) for item in addresses):
        raise CaptureError("HTTP capture requires public DNS addresses")
    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        # Connect to the validated sockaddr directly: no second DNS lookup that
        # could resolve the hostname to a private address after validation.
        family, socktype, proto, _, sockaddr = addresses[0]
        connection.sock = socket.socket(family, socktype, proto)
        connection.sock.settimeout(timeout)
        connection.sock.connect(sockaddr)
        if parsed.scheme == "https":
            context = ssl.create_default_context()
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.set_alpn_protocols(["http/1.1"])
            connection.sock = context.wrap_socket(connection.sock, server_hostname=host)
        target = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        connection.request("GET", target, headers={"Accept-Encoding": "identity"})
        with connection.getresponse() as response:
            yield response
    finally:
        connection.close()


class SnapshotCapture:
    def __init__(
        self,
        root: Path,
        *,
        limits: CaptureLimits | None = None,
        transport: HTTPTransport = public_http,
    ):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("snapshot root must be a directory")
        self.limits = limits or CaptureLimits()
        self.transport = transport

    def _storage(self) -> Path:
        storage = self.root
        for part in (".lineage", "snapshots"):
            storage /= part
            if storage.is_symlink():
                raise CaptureError("managed snapshot directories must not be symlinks")
            storage.mkdir(exist_ok=True)
        return storage

    @contextmanager
    def _errors(self):
        try:
            yield
        except CaptureError:
            raise
        except (OSError, ValueError, http.client.HTTPException) as error:
            # Do not echo URLs, source contents, or transport error text into logs.
            raise CaptureError("source capture failed during file or HTTP transfer") from error

    def local(self, path: str) -> CapturedBytes:
        with self._errors():
            relative = Path(path)
            if relative.is_absolute():
                raise CaptureError("local capture requires a project-relative path")
            source = (self.root / relative).resolve(strict=True)
            if not source.is_relative_to(self.root):
                raise CaptureError("source path escapes the project root")
            if not source.is_file():
                raise CaptureError("source must be a regular file")
            with source.open("rb") as stream:
                return self._copy(
                    stream, {"kind": "local", "path": source.relative_to(self.root).as_posix()}
                )

    def bytes(self, data: bytes) -> CapturedBytes:
        """Retain generated content without inventing a SourceSnapshot graph node."""
        with self._errors():
            return self._copy(io.BytesIO(data), {"kind": "generated"})

    def http(self, url: str) -> CapturedBytes:
        with self._errors():
            for hop in range(self.limits.max_redirects + 1):
                parsed = _url(url)
                url = parsed._replace(fragment="").geturl()
                with self.transport(url, self.limits.timeout_seconds) as response:
                    if response.status in _REDIRECTS:
                        location = response.getheader("Location")
                        if not location or hop == self.limits.max_redirects:
                            raise CaptureError(
                                "HTTP redirect is missing a target or exceeds the limit"
                            )
                        _url_characters(location)
                        url = urljoin(url, location)
                        continue
                    if response.status != 200:
                        raise CaptureError(f"HTTP capture returned status {response.status}")
                    if response.getheader("Content-Encoding", "identity").lower() != "identity":
                        raise CaptureError("encoded HTTP responses are not supported")
                    length = response.getheader("Content-Length")
                    expected = None
                    if length is not None:
                        if not length.isascii() or not length.isdecimal():
                            raise CaptureError("invalid HTTP content length")
                        expected = int(length)
                        if expected > self.limits.max_bytes:
                            raise CaptureError("source exceeds the capture byte limit")
                    return self._copy(
                        response, {"kind": "http", "final_url": url}, expected=expected
                    )
        raise CaptureError("HTTP capture did not return content")

    def _copy(
        self,
        stream: BinaryIO | HTTPResponse,
        retrieval: dict[str, str],
        *,
        expected: int | None = None,
    ) -> CapturedBytes:
        storage = self._storage()
        temporary = None
        try:
            digest = hashlib.sha256()
            size = 0
            with tempfile.NamedTemporaryFile(
                dir=storage, prefix=".capture-", delete=False
            ) as output:
                temporary = Path(output.name)
                while chunk := stream.read(min(_CHUNK, self.limits.max_bytes - size + 1)):
                    size += len(chunk)
                    if size > self.limits.max_bytes:
                        raise CaptureError("source exceeds the capture byte limit")
                    digest.update(chunk)
                    output.write(chunk)
                if expected is not None and size != expected:
                    raise CaptureError("HTTP content length does not match captured bytes")
                output.flush()
                os.fsync(output.fileno())
            content_hash = f"sha256:{digest.hexdigest()}"
            blob = storage / digest.hexdigest()
            try:
                # Atomic create without replacing a winner from another capture.
                os.link(temporary, blob)
            except FileExistsError:
                if blob.is_symlink() or not blob.is_file() or hash_file(blob) != content_hash:
                    raise CaptureError("existing snapshot blob is corrupt or unsafe") from None
            return CapturedBytes(
                content_hash, size, blob.relative_to(self.root).as_posix(), retrieval
            )
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
