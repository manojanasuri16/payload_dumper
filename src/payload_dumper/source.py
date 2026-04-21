"""Byte sources for payload data.

Abstracts over the *where* the payload bytes live: a local file, an HTTP(S)
URL served with Range support, or a member inside a ZIP archive (local or
remote). Everything downstream reads via `ByteSource.read_at(offset, length)`,
so `parse_payload` and `extract_partition` don't care about transport.
"""

from __future__ import annotations

import io
import os
import threading
import zipfile
from abc import ABC, abstractmethod
from typing import Optional

OTA_PAYLOAD_MEMBER = "payload.bin"


class SourceError(Exception):
    """Raised when a source cannot be opened or read."""


class ByteSource(ABC):
    """Random-access, thread-safe, read-only byte source."""

    @abstractmethod
    def read_at(self, offset: int, length: int) -> bytes: ...

    @abstractmethod
    def size(self) -> int: ...

    def close(self) -> None:
        """Release any held resources. Safe to call multiple times."""


class FileSource(ByteSource):
    """Local file. Each thread lazily opens its own handle (avoids seek races)."""

    def __init__(self, path: str):
        if not os.path.isfile(path):
            raise FileNotFoundError(path)
        self._path = path
        self._size = os.path.getsize(path)
        self._local = threading.local()
        self._handles: list = []
        self._handles_lock = threading.Lock()

    def _handle(self):
        fh = getattr(self._local, "fh", None)
        if fh is None:
            fh = open(self._path, "rb")
            self._local.fh = fh
            with self._handles_lock:
                self._handles.append(fh)
        return fh

    def read_at(self, offset: int, length: int) -> bytes:
        fh = self._handle()
        fh.seek(offset)
        return fh.read(length)

    def size(self) -> int:
        return self._size

    def close(self) -> None:
        with self._handles_lock:
            for fh in self._handles:
                try:
                    fh.close()
                except Exception:
                    pass
            self._handles.clear()


class HttpSource(ByteSource):
    """HTTP(S) URL read via Range requests. Requires the server to report
    Content-Length and support byte ranges — Google/OEM OTA mirrors do."""

    def __init__(self, url: str, *, timeout: float = 30.0):
        import httpx  # imported lazily so offline users of FileSource don't need it

        self._url = url
        self._client = httpx.Client(
            follow_redirects=True,
            timeout=timeout,
            headers={"User-Agent": "android-payload-dumper"},
        )

        # Some servers refuse HEAD; fall back to a 0-byte ranged GET to probe.
        probe = self._client.head(url)
        if probe.status_code >= 400:
            probe = self._client.get(url, headers={"Range": "bytes=0-0"})
        probe.raise_for_status()

        headers = {k.lower(): v for k, v in probe.headers.items()}
        if "content-length" in headers:
            self._size = int(headers["content-length"])
        elif "content-range" in headers:
            # "bytes 0-0/12345"
            self._size = int(headers["content-range"].rsplit("/", 1)[-1])
        else:
            raise SourceError(f"server did not report size for {url}")

        if headers.get("accept-ranges", "").lower() != "bytes":
            # Probe with a real range to confirm — some servers omit the header
            # but still honor Range (especially behind CDNs).
            test = self._client.get(url, headers={"Range": "bytes=0-3"})
            if test.status_code != 206:
                raise SourceError(
                    f"server does not support byte-range requests for {url}"
                )

    def read_at(self, offset: int, length: int) -> bytes:
        if length <= 0:
            return b""
        end = offset + length - 1
        r = self._client.get(self._url, headers={"Range": f"bytes={offset}-{end}"})
        if r.status_code not in (200, 206):
            raise SourceError(
                f"HTTP {r.status_code} fetching bytes={offset}-{end} from {self._url}"
            )
        return r.content

    def size(self) -> int:
        return self._size

    def close(self) -> None:
        self._client.close()


class ZipMemberSource(ByteSource):
    """A single uncompressed (ZIP_STORED) member inside a ZIP archive on top
    of another ByteSource. Android OTA zips store `payload.bin` uncompressed,
    so every offset into the member maps directly to an offset in the zip."""

    def __init__(self, parent: ByteSource, member_name: str = OTA_PAYLOAD_MEMBER):
        self._parent = parent
        self._member = member_name

        proxy = _ByteSourceFile(parent)
        with zipfile.ZipFile(proxy) as zf:
            try:
                info = zf.getinfo(member_name)
            except KeyError as e:
                raise SourceError(
                    f"ZIP does not contain a '{member_name}' member"
                ) from e

            if info.compress_type != zipfile.ZIP_STORED:
                raise SourceError(
                    f"'{member_name}' is compressed inside the ZIP "
                    f"(compress_type={info.compress_type}); only ZIP_STORED "
                    "members are supported for streaming extraction"
                )

            # Local file header: [sig 4][...24][name_len 2][extra_len 2][name][extra]
            proxy.seek(info.header_offset)
            local = proxy.read(30)
            name_len = int.from_bytes(local[26:28], "little")
            extra_len = int.from_bytes(local[28:30], "little")
            self._base = info.header_offset + 30 + name_len + extra_len
            self._size = info.file_size

    def read_at(self, offset: int, length: int) -> bytes:
        return self._parent.read_at(self._base + offset, length)

    def size(self) -> int:
        return self._size

    def close(self) -> None:
        self._parent.close()


class _ByteSourceFile(io.RawIOBase):
    """File-like adapter over a ByteSource — lets `zipfile.ZipFile` consume it."""

    def __init__(self, source: ByteSource):
        self._source = source
        self._pos = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            self._pos = offset
        elif whence == io.SEEK_CUR:
            self._pos += offset
        elif whence == io.SEEK_END:
            self._pos = self._source.size() + offset
        return self._pos

    def tell(self) -> int:
        return self._pos

    def readinto(self, b) -> int:  # type: ignore[override]
        remaining = self._source.size() - self._pos
        if remaining <= 0:
            return 0
        n = min(len(b), remaining)
        data = self._source.read_at(self._pos, n)
        b[: len(data)] = data
        self._pos += len(data)
        return len(data)


def open_source(target: str) -> ByteSource:
    """Open a byte source from a local path or URL. If the target is a ZIP
    (by extension), drill into `payload.bin` inside it automatically."""
    is_url = target.startswith(("http://", "https://"))
    is_zip = target.lower().endswith(".zip")

    if is_url:
        base: ByteSource = HttpSource(target)
    else:
        base = FileSource(target)

    if is_zip:
        try:
            return ZipMemberSource(base)
        except Exception:
            base.close()
            raise
    return base


def describe(target: str) -> str:
    """One-line label for the source, used in the CLI banner."""
    if target.startswith(("http://", "https://")):
        kind = "url"
    elif target.lower().endswith(".zip"):
        kind = "zip"
    else:
        kind = "file"
    return f"{kind}:{target}"
