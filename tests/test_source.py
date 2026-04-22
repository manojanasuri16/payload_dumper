"""Tests for the ByteSource implementations."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from payload_dumper.source import (
    FileSource,
    HttpSource,
    SourceError,
    ZipMemberSource,
    describe,
    open_source,
)
from tests.conftest import wrap_in_zip


# --- HTTP server helper --------------------------------------------------


def _make_range_server(payload: bytes, *, serve_accept_ranges: bool = True):
    """Tiny HTTP server that honors `Range: bytes=start-end`. Returns (url, shutdown)."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a, **kw):
            pass

        def _write_range(self):
            rng = self.headers.get("Range")
            if not rng:
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                if serve_accept_ranges:
                    self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                if self.command == "GET":
                    self.wfile.write(payload)
                return

            # "bytes=start-end"
            spec = rng.split("=", 1)[1]
            start_s, end_s = spec.split("-", 1)
            start = int(start_s)
            end = int(end_s) if end_s else len(payload) - 1
            end = min(end, len(payload) - 1)
            chunk = payload[start : end + 1]
            self.send_response(206)
            self.send_header("Content-Length", str(len(chunk)))
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(payload)}")
            if serve_accept_ranges:
                self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            if self.command == "GET":
                self.wfile.write(chunk)

        def do_HEAD(self):
            self._write_range()

        def do_GET(self):
            self._write_range()

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/payload.bin"

    def shutdown():
        server.shutdown()
        server.server_close()

    return url, shutdown


@pytest.fixture
def http_server(multi_partition_payload):
    url, shutdown = _make_range_server(multi_partition_payload)
    yield url, multi_partition_payload
    shutdown()


# --- FileSource ----------------------------------------------------------


class TestFileSource:
    def test_round_trip(self, payload_file, simple_payload_bytes):
        src = FileSource(payload_file)
        try:
            assert src.size() == len(simple_payload_bytes)
            assert src.read_at(0, 4) == b"CrAU"
            assert src.read_at(0, src.size()) == simple_payload_bytes
        finally:
            src.close()

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            FileSource(str(tmp_path / "does-not-exist.bin"))

    def test_concurrent_reads_are_independent(self, payload_file, simple_payload_bytes):
        """Each thread must get its own handle so parallel seek/read doesn't race."""
        src = FileSource(payload_file)
        results: list[bytes] = [b""] * 16
        errors: list[Exception] = []

        def reader(i: int):
            try:
                # read a different slice from each thread, many times
                for _ in range(50):
                    offset = (i * 7) % max(1, src.size() - 4)
                    results[i] = src.read_at(offset, 4)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=reader, args=(i,)) for i in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        src.close()
        assert not errors
        # Each thread's last read should match the source at its computed offset
        for i, got in enumerate(results):
            offset = (i * 7) % max(1, len(simple_payload_bytes) - 4)
            assert got == simple_payload_bytes[offset : offset + 4]

    def test_close_is_idempotent(self, payload_file):
        src = FileSource(payload_file)
        src.read_at(0, 4)
        src.close()
        src.close()  # must not raise


# --- ZipMemberSource -----------------------------------------------------


class TestZipMemberSource:
    def test_reads_stored_member(self, tmp_path, multi_partition_payload):
        zip_bytes = wrap_in_zip(multi_partition_payload)
        p = tmp_path / "ota.zip"
        p.write_bytes(zip_bytes)

        zsrc = ZipMemberSource(FileSource(str(p)))
        try:
            assert zsrc.size() == len(multi_partition_payload)
            assert zsrc.read_at(0, 4) == b"CrAU"
            assert zsrc.read_at(0, zsrc.size()) == multi_partition_payload
        finally:
            zsrc.close()

    def test_rejects_deflated_member(self, tmp_path, simple_payload_bytes):
        zip_bytes = wrap_in_zip(simple_payload_bytes, compressed=True)
        p = tmp_path / "ota.zip"
        p.write_bytes(zip_bytes)

        with pytest.raises(SourceError, match="ZIP_STORED"):
            ZipMemberSource(FileSource(str(p)))

    def test_missing_member(self, tmp_path, simple_payload_bytes):
        zip_bytes = wrap_in_zip(simple_payload_bytes, member="other.bin")
        p = tmp_path / "ota.zip"
        p.write_bytes(zip_bytes)

        with pytest.raises(SourceError, match="payload.bin"):
            ZipMemberSource(FileSource(str(p)))


# --- HttpSource ----------------------------------------------------------


class TestHttpSource:
    def test_round_trip(self, http_server):
        url, payload = http_server
        src = HttpSource(url)
        try:
            assert src.size() == len(payload)
            assert src.read_at(0, 4) == b"CrAU"
            # read a middle slice
            assert src.read_at(100, 64) == payload[100:164]
        finally:
            src.close()

    def test_zero_length_read_is_empty(self, http_server):
        url, _ = http_server
        src = HttpSource(url)
        try:
            assert src.read_at(0, 0) == b""
        finally:
            src.close()


# --- open_source dispatch ------------------------------------------------


class TestOpenSource:
    def test_plain_file_returns_filesource(self, payload_file, simple_payload_bytes):
        src = open_source(payload_file)
        try:
            assert isinstance(src, FileSource)
            assert src.size() == len(simple_payload_bytes)
        finally:
            src.close()

    def test_zip_extension_drills_into_payload(self, payload_zip_file, multi_partition_payload):
        src = open_source(payload_zip_file)
        try:
            assert isinstance(src, ZipMemberSource)
            assert src.size() == len(multi_partition_payload)
            assert src.read_at(0, 4) == b"CrAU"
        finally:
            src.close()

    def test_zip_open_failure_closes_base(self, tmp_path):
        # Not a valid zip at all — opening should fail and not leak the FileSource
        p = tmp_path / "bogus.zip"
        p.write_bytes(b"not a zip")
        with pytest.raises(Exception):
            open_source(str(p))


class TestDescribe:
    def test_url(self):
        assert describe("https://example.com/a.zip").startswith("url:")

    def test_zip(self, tmp_path):
        assert describe(str(tmp_path / "a.zip")).startswith("zip:")

    def test_file(self, tmp_path):
        assert describe(str(tmp_path / "payload.bin")).startswith("file:")
