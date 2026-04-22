"""Shared fixtures: synthesize real CrAU payload.bin blobs in memory.

The tests don't use canned payloads — they build a `DeltaArchiveManifest`
from scratch, pack the CrAU header around it, and hand the resulting bytes
to the real `parse_payload` / `extract_partition` code. That way the tests
exercise the actual format, not a mock."""

from __future__ import annotations

import bz2
import hashlib
import io
import lzma
import struct
import zipfile
from dataclasses import dataclass
from typing import List

import pytest

from payload_dumper import update_metadata_pb2 as um

BLOCK_SIZE = 4096
MAGIC = b"CrAU"

OP_REPLACE = 0
OP_REPLACE_BZ = 1
OP_REPLACE_XZ = 8
OP_ZERO = 6
OP_SOURCE_COPY = 4  # unsupported — for delta-rejection tests


@dataclass
class OpSpec:
    """Human-readable description of a single op; compiled into protobuf later."""
    op_type: int
    payload: bytes = b""          # decompressed bytes this op contributes
    encoding: str = "raw"         # "raw" | "bz2" | "xz" | "lzma-alone" | "lzma-raw2"
    zero_bytes: int = 0           # for OP_ZERO: how many zero bytes to emit


def _compress(data: bytes, encoding: str) -> bytes:
    if encoding == "raw":
        return data
    if encoding == "bz2":
        return bz2.compress(data)
    if encoding == "xz":
        return lzma.compress(data, format=lzma.FORMAT_XZ)
    if encoding == "lzma-alone":
        return lzma.compress(data, format=lzma.FORMAT_ALONE)
    if encoding == "lzma-raw2":
        return lzma.compress(
            data, format=lzma.FORMAT_RAW, filters=[{"id": lzma.FILTER_LZMA2, "preset": 6}]
        )
    raise ValueError(encoding)


def build_payload(partitions: List[tuple[str, List[OpSpec]]], *, version: int = 2) -> bytes:
    """Build a full CrAU payload for the given [(name, [OpSpec, ...])] list.

    Returns the concatenated header + manifest + (zero-length signature) + blobs.
    Computes all hashes and offsets honestly so the parser accepts it."""
    manifest = um.DeltaArchiveManifest()
    manifest.minor_version = 0
    manifest.block_size = BLOCK_SIZE

    blobs = bytearray()
    blob_cursor = 0

    for name, ops in partitions:
        part = manifest.partitions.add()
        part.partition_name = name
        image = bytearray()

        for spec in ops:
            op = part.operations.add()
            op.type = spec.op_type
            if spec.op_type == OP_ZERO:
                # ZERO: no data in blobs, size comes from dst_extents
                ext = op.dst_extents.add()
                ext.start_block = 0
                ext.num_blocks = max(1, (spec.zero_bytes + BLOCK_SIZE - 1) // BLOCK_SIZE)
                # pad payload to match the block-rounded extent
                image.extend(b"\x00" * (ext.num_blocks * BLOCK_SIZE))
            else:
                raw = _compress(spec.payload, spec.encoding)
                op.data_offset = blob_cursor
                op.data_length = len(raw)
                op.data_sha256_hash = hashlib.sha256(raw).digest()
                blobs.extend(raw)
                blob_cursor += len(raw)
                image.extend(spec.payload)

        part.new_partition_info.size = len(image)
        part.new_partition_info.hash = hashlib.sha256(bytes(image)).digest()

    manifest_bytes = manifest.SerializeToString()
    metadata_sig = b""

    out = bytearray()
    out.extend(MAGIC)
    out.extend(struct.pack(">Q", version))
    out.extend(struct.pack(">Q", len(manifest_bytes)))
    if version > 1:
        out.extend(struct.pack(">I", len(metadata_sig)))
    out.extend(manifest_bytes)
    out.extend(metadata_sig)
    out.extend(blobs)
    return bytes(out)


def wrap_in_zip(payload_bytes: bytes, *, member: str = "payload.bin", compressed: bool = False) -> bytes:
    """Wrap `payload_bytes` into a ZIP archive. ZIP_STORED by default to match
    real OTA archives; set `compressed=True` to exercise the rejection path."""
    buf = io.BytesIO()
    compression = zipfile.ZIP_DEFLATED if compressed else zipfile.ZIP_STORED
    with zipfile.ZipFile(buf, "w", compression=compression) as zf:
        zf.writestr(member, payload_bytes)
    return buf.getvalue()


# --- fixtures ------------------------------------------------------------


@pytest.fixture
def simple_payload_bytes() -> bytes:
    """One partition, one REPLACE op. Smallest valid payload."""
    return build_payload([
        ("boot", [OpSpec(OP_REPLACE, b"boot-image-bytes" * 32)]),
    ])


@pytest.fixture
def multi_partition_payload() -> bytes:
    """Three partitions covering every supported op/encoding."""
    return build_payload([
        ("boot", [
            OpSpec(OP_REPLACE, b"BOOT" * 64),
        ]),
        ("system", [
            OpSpec(OP_REPLACE_XZ, b"system-chunk-1" * 100, encoding="xz"),
            OpSpec(OP_REPLACE_BZ, b"system-chunk-2" * 100, encoding="bz2"),
            OpSpec(OP_ZERO, zero_bytes=BLOCK_SIZE * 2),
        ]),
        ("vendor", [
            OpSpec(OP_REPLACE_XZ, b"vendor-chunk" * 200, encoding="xz"),
        ]),
    ])


@pytest.fixture
def delta_payload() -> bytes:
    """Contains SOURCE_COPY — should be rejected by validate_operations."""
    return build_payload([
        ("system", [OpSpec(OP_SOURCE_COPY)]),
    ])


@pytest.fixture
def payload_file(tmp_path, simple_payload_bytes):
    p = tmp_path / "payload.bin"
    p.write_bytes(simple_payload_bytes)
    return str(p)


@pytest.fixture
def multi_payload_file(tmp_path, multi_partition_payload):
    p = tmp_path / "payload.bin"
    p.write_bytes(multi_partition_payload)
    return str(p)


@pytest.fixture
def payload_zip_file(tmp_path, multi_partition_payload):
    zip_bytes = wrap_in_zip(multi_partition_payload)
    p = tmp_path / "firmware_ota.zip"
    p.write_bytes(zip_bytes)
    return str(p)
