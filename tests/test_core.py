"""Tests for parse_payload, validate_operations, extract_partition, _decompress."""

from __future__ import annotations

import hashlib
import lzma
import os
import struct

import pytest

from payload_dumper.core import (
    PayloadError,
    UnsupportedPayloadError,
    _decompress,
    extract_partition,
    parse_payload,
    partition_op_types,
    validate_operations,
)
from payload_dumper.source import FileSource
from tests.conftest import OP_REPLACE, OP_REPLACE_XZ, OpSpec, build_payload


def _source_from_bytes(tmp_path, data: bytes) -> FileSource:
    p = tmp_path / "p.bin"
    p.write_bytes(data)
    return FileSource(str(p))


class TestParsePayload:
    def test_basic_header(self, tmp_path, simple_payload_bytes):
        src = _source_from_bytes(tmp_path, simple_payload_bytes)
        try:
            payload = parse_payload(src)
            assert payload.manifest.block_size == 4096
            assert len(payload.manifest.partitions) == 1
            assert payload.manifest.partitions[0].partition_name == "boot"
            # data_offset should be past header + manifest (+ 0-byte signature)
            assert payload.data_offset > 20
        finally:
            src.close()

    def test_invalid_magic_rejected(self, tmp_path, simple_payload_bytes):
        bad = b"FAKE" + simple_payload_bytes[4:]
        src = _source_from_bytes(tmp_path, bad)
        try:
            with pytest.raises(PayloadError, match="magic"):
                parse_payload(src)
        finally:
            src.close()

    def test_unsupported_version_rejected(self, tmp_path, simple_payload_bytes):
        bad = bytearray(simple_payload_bytes)
        bad[4:12] = struct.pack(">Q", 99)  # version 99
        src = _source_from_bytes(tmp_path, bytes(bad))
        try:
            with pytest.raises(PayloadError, match="version"):
                parse_payload(src)
        finally:
            src.close()

    def test_v1_payload_has_no_signature_field(self, tmp_path):
        # Build a v1 payload (no metadata signature size field)
        data = build_payload([("boot", [OpSpec(OP_REPLACE, b"hi" * 64)])], version=1)
        src = _source_from_bytes(tmp_path, data)
        try:
            p = parse_payload(src)
            # header_tail=20 for v1, no signature bytes
            # data_offset = 20 + len(manifest)
            assert p.data_offset >= 20
            assert p.manifest.partitions[0].partition_name == "boot"
        finally:
            src.close()


class TestValidateOperations:
    def test_full_ota_passes(self, tmp_path, multi_partition_payload):
        src = _source_from_bytes(tmp_path, multi_partition_payload)
        try:
            payload = parse_payload(src)
            validate_operations(payload.manifest.partitions)  # does not raise
        finally:
            src.close()

    def test_delta_ota_rejected(self, tmp_path, delta_payload):
        src = _source_from_bytes(tmp_path, delta_payload)
        try:
            payload = parse_payload(src)
            with pytest.raises(UnsupportedPayloadError, match="SOURCE_COPY"):
                validate_operations(payload.manifest.partitions)
        finally:
            src.close()


class TestExtractPartition:
    def test_round_trip_all_supported_ops(self, tmp_path, multi_partition_payload):
        src = _source_from_bytes(tmp_path, multi_partition_payload)
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        try:
            payload = parse_payload(src)
            progress_calls = []
            for part in payload.manifest.partitions:
                path = extract_partition(
                    src, payload.data_offset, part, str(out_dir),
                    on_progress=lambda n, name=part.partition_name: progress_calls.append((name, n)),
                )
                # hash matches manifest
                img = open(path, "rb").read()
                assert hashlib.sha256(img).digest() == part.new_partition_info.hash
                assert len(img) == part.new_partition_info.size

            # one progress tick per op, per partition
            expected_ticks = sum(len(p.operations) for p in payload.manifest.partitions)
            assert len(progress_calls) == expected_ticks
        finally:
            src.close()

    def test_op_hash_mismatch_raises(self, tmp_path):
        """Corrupt one op's data_sha256_hash and the extractor must catch it."""
        from payload_dumper import update_metadata_pb2 as um

        data = build_payload([("boot", [OpSpec(OP_REPLACE, b"hello" * 64)])])
        src = _source_from_bytes(tmp_path, data)
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        try:
            payload = parse_payload(src)
            # Mutate the in-memory manifest copy to have a bogus hash
            part = payload.manifest.partitions[0]
            part.operations[0].data_sha256_hash = b"\x00" * 32
            with pytest.raises(PayloadError, match="hash mismatch"):
                extract_partition(src, payload.data_offset, part, str(out_dir))
        finally:
            src.close()

    def test_partition_hash_mismatch_raises(self, tmp_path):
        data = build_payload([("boot", [OpSpec(OP_REPLACE, b"abc" * 64)])])
        src = _source_from_bytes(tmp_path, data)
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        try:
            payload = parse_payload(src)
            part = payload.manifest.partitions[0]
            part.new_partition_info.hash = b"\x00" * 32
            with pytest.raises(PayloadError, match="final image hash mismatch"):
                extract_partition(src, payload.data_offset, part, str(out_dir))
        finally:
            src.close()

    def test_missing_hashes_are_allowed(self, tmp_path):
        """Old payloads don't populate hashes — don't fail the extraction."""
        data = build_payload([("boot", [OpSpec(OP_REPLACE, b"xyz" * 64)])])
        src = _source_from_bytes(tmp_path, data)
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        try:
            payload = parse_payload(src)
            part = payload.manifest.partitions[0]
            part.operations[0].data_sha256_hash = b""
            part.new_partition_info.hash = b""
            path = extract_partition(src, payload.data_offset, part, str(out_dir))
            assert os.path.exists(path)
        finally:
            src.close()


class TestDecompressFallbacks:
    """_decompress tries FORMAT_XZ → FORMAT_ALONE → FORMAT_RAW+LZMA2 for
    REPLACE_XZ; each framing appears in real OEM payloads."""

    def test_xz_format(self):
        plain = b"hello world " * 100
        xz = lzma.compress(plain, format=lzma.FORMAT_XZ)
        assert _decompress(xz, OP_REPLACE_XZ) == plain

    def test_lzma_alone_format(self):
        plain = b"ancient lzma framing " * 50
        alone = lzma.compress(plain, format=lzma.FORMAT_ALONE)
        assert _decompress(alone, OP_REPLACE_XZ) == plain

    def test_lzma_raw_lzma2(self):
        plain = b"raw lzma2 stream " * 40
        raw = lzma.compress(
            plain, format=lzma.FORMAT_RAW, filters=[{"id": lzma.FILTER_LZMA2, "preset": 6}]
        )
        assert _decompress(raw, OP_REPLACE_XZ) == plain


class TestPartitionOpTypes:
    def test_deduped_and_sorted(self, tmp_path, multi_partition_payload):
        src = _source_from_bytes(tmp_path, multi_partition_payload)
        try:
            payload = parse_payload(src)
            system = next(p for p in payload.manifest.partitions if p.partition_name == "system")
            types = partition_op_types(system)
            # system has REPLACE_XZ, REPLACE_BZ, ZERO — alphabetized, deduped
            assert types == sorted(set(types))
            assert "REPLACE_XZ" in types
            assert "REPLACE_BZ" in types
            assert "ZERO" in types
        finally:
            src.close()
