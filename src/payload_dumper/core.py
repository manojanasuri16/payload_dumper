"""Parsing and extraction for Android OTA payload.bin (CrAU format)."""

import bz2
import hashlib
import lzma
import os
import struct
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional

from . import update_metadata_pb2 as um
from .source import ByteSource

PAYLOAD_MAGIC = b"CrAU"
BLOCK_SIZE = 4096

OP_REPLACE = 0
OP_REPLACE_BZ = 1
OP_REPLACE_XZ = 8
OP_ZERO = 6
OP_DISCARD = 7

SUPPORTED_OPS = {OP_REPLACE, OP_REPLACE_BZ, OP_REPLACE_XZ, OP_ZERO, OP_DISCARD}

OP_NAMES = {
    0: "REPLACE",
    1: "REPLACE_BZ",
    2: "MOVE",
    3: "BSDIFF",
    4: "SOURCE_COPY",
    5: "SOURCE_BSDIFF",
    6: "ZERO",
    7: "DISCARD",
    8: "REPLACE_XZ",
    9: "PUFFDIFF",
    10: "BROTLI_BSDIFF",
    11: "LZ4DIFF_BSDIFF",
    12: "LZ4DIFF_PUFFDIFF",
    13: "ZUCCHINI",
}


class PayloadError(Exception):
    """Raised when a payload is malformed or fails integrity checks."""


class UnsupportedPayloadError(PayloadError):
    """Raised for delta/incremental OTAs or any op outside SUPPORTED_OPS."""


@dataclass
class Payload:
    manifest: "um.DeltaArchiveManifest"
    data_offset: int


def _u32(data: bytes) -> int:
    return struct.unpack(">I", data)[0]


def _u64(data: bytes) -> int:
    return struct.unpack(">Q", data)[0]


def parse_payload(source: ByteSource) -> Payload:
    """Parse the CrAU header from a ByteSource; return the manifest and the
    absolute offset at which the data blobs begin (all op.data_offset values
    are relative to this)."""
    magic = source.read_at(0, 4)
    if magic != PAYLOAD_MAGIC:
        raise PayloadError(
            f"invalid payload magic: expected {PAYLOAD_MAGIC!r}, got {magic!r}"
        )

    version = _u64(source.read_at(4, 8))
    if version not in (1, 2):
        raise PayloadError(f"unsupported payload version: {version}")

    manifest_size = _u64(source.read_at(12, 8))

    header_tail = 20
    if version > 1:
        metadata_sig_size = _u32(source.read_at(20, 4))
        header_tail = 24
    else:
        metadata_sig_size = 0

    manifest_bytes = source.read_at(header_tail, manifest_size)
    data_offset = header_tail + manifest_size + metadata_sig_size

    dam = um.DeltaArchiveManifest()
    dam.ParseFromString(manifest_bytes)
    return Payload(manifest=dam, data_offset=data_offset)


def validate_operations(partitions: Iterable) -> None:
    """Reject delta OTAs up-front so we don't half-write an image before failing."""
    for part in partitions:
        for op in part.operations:
            if op.type not in SUPPORTED_OPS:
                name = OP_NAMES.get(op.type, f"UNKNOWN({op.type})")
                raise UnsupportedPayloadError(
                    f"partition '{part.partition_name}' uses unsupported op "
                    f"'{name}' — likely an incremental (delta) OTA. Only full "
                    "OTA payloads are supported."
                )


def _decompress(data: bytes, op_type: int) -> bytes:
    if op_type == OP_REPLACE:
        return data
    if op_type == OP_REPLACE_BZ:
        return bz2.decompress(data)
    if op_type == OP_REPLACE_XZ:
        # Different OEMs ship LZMA in different framings; try each.
        for fmt in (lzma.FORMAT_XZ, lzma.FORMAT_ALONE):
            try:
                return lzma.decompress(data, format=fmt)
            except lzma.LZMAError:
                continue
        return lzma.decompress(
            data, format=lzma.FORMAT_RAW, filters=[{"id": lzma.FILTER_LZMA2}]
        )
    raise ValueError(f"cannot decompress op: {OP_NAMES.get(op_type, op_type)}")


def extract_partition(
    source: ByteSource,
    data_offset: int,
    part,
    output_dir: str,
    on_progress: Optional[Callable[[int], None]] = None,
) -> str:
    """Extract one partition to `<output_dir>/<name>.img` and return the path.

    The `source` must be thread-safe for concurrent `read_at` calls — all of
    FileSource, HttpSource, and ZipMemberSource are. `on_progress(n)` is
    invoked once per completed operation.
    """
    out_path = os.path.join(output_dir, f"{part.partition_name}.img")
    digest = hashlib.sha256()

    with open(out_path, "wb") as out_file:
        for op in part.operations:
            if op.type in (OP_ZERO, OP_DISCARD):
                total = sum(ext.num_blocks for ext in op.dst_extents) * BLOCK_SIZE
                data = b"\x00" * total
            else:
                raw = source.read_at(data_offset + op.data_offset, op.data_length)
                if (
                    op.data_sha256_hash
                    and hashlib.sha256(raw).digest() != op.data_sha256_hash
                ):
                    raise PayloadError(
                        f"{part.partition_name}: operation data hash mismatch"
                    )
                data = _decompress(raw, op.type)

            digest.update(data)
            out_file.write(data)
            if on_progress is not None:
                on_progress(1)

    expected = part.new_partition_info.hash
    if expected and digest.digest() != expected:
        raise PayloadError(f"{part.partition_name}: final image hash mismatch")
    return out_path


def partition_op_types(part) -> List[str]:
    """Sorted, de-duplicated op-type names for a partition — for listing."""
    return sorted({OP_NAMES.get(op.type, f"UNKNOWN({op.type})") for op in part.operations})
