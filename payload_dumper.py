#!/usr/bin/env python3
"""Extract partition images from Android OTA payload.bin files."""

import argparse
import bz2
import hashlib
import io
import lzma
import os
import struct
import sys

try:
    import brotli
    HAS_BROTLI = True
except ImportError:
    HAS_BROTLI = False

import update_metadata_pb2 as um

PAYLOAD_MAGIC = b"CrAU"
BLOCK_SIZE = 4096

# Operation type constants
OP_REPLACE = 0
OP_REPLACE_BZ = 1
OP_REPLACE_XZ = 8
OP_ZERO = 6
OP_DISCARD = 7
OP_BROTLI_BSDIFF = 10

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


def u32(data: bytes) -> int:
    return struct.unpack(">I", data)[0]


def u64(data: bytes) -> int:
    return struct.unpack(">Q", data)[0]


def verify_contiguous(exts) -> bool:
    blocks = 0
    for ext in exts:
        if ext.start_block != blocks:
            return False
        blocks += ext.num_blocks
    return True


def decompress_payload(data: bytes, op_type: int) -> bytes:
    """Decompress operation data based on operation type."""
    if op_type == OP_REPLACE:
        return data

    if op_type == OP_REPLACE_BZ:
        return bz2.decompress(data)

    if op_type == OP_REPLACE_XZ:
        # Try XZ container format first, then raw LZMA stream
        try:
            return lzma.decompress(data, format=lzma.FORMAT_XZ)
        except lzma.LZMAError:
            pass
        try:
            return lzma.decompress(data, format=lzma.FORMAT_ALONE)
        except lzma.LZMAError:
            pass
        return lzma.decompress(data, format=lzma.FORMAT_RAW,
                               filters=[{"id": lzma.FILTER_LZMA2}])

    if op_type == OP_ZERO or op_type == OP_DISCARD:
        return b""

    raise ValueError(f"Unsupported operation type: {OP_NAMES.get(op_type, op_type)}")


def data_for_op(payload_file: io.BufferedReader, data_offset: int, op) -> bytes:
    """Read and decompress data for a single operation."""
    if op.type in (OP_ZERO, OP_DISCARD):
        # Calculate total blocks from dst_extents
        total_blocks = sum(ext.num_blocks for ext in op.dst_extents)
        return b"\x00" * (total_blocks * BLOCK_SIZE)

    payload_file.seek(data_offset + op.data_offset)
    data = payload_file.read(op.data_length)

    if op.data_sha256_hash:
        actual_hash = hashlib.sha256(data).digest()
        if actual_hash != op.data_sha256_hash:
            raise ValueError("Operation data hash mismatch")

    return decompress_payload(data, op.type)


def dump_part(payload_file: io.BufferedReader, data_offset: int,
              part, output_dir: str) -> None:
    """Extract a single partition to an image file."""
    out_path = os.path.join(output_dir, f"{part.partition_name}.img")
    num_ops = len(part.operations)

    sys.stdout.write(f"  {part.partition_name} ({num_ops} ops)...")
    sys.stdout.flush()

    with open(out_path, "wb") as out_file:
        h = hashlib.sha256()
        for i, op in enumerate(part.operations):
            data = data_for_op(payload_file, data_offset, op)
            h.update(data)
            out_file.write(data)

            # Progress indicator
            if num_ops > 20 and (i + 1) % max(1, num_ops // 20) == 0:
                pct = (i + 1) * 100 // num_ops
                sys.stdout.write(f"\r  {part.partition_name} ({num_ops} ops)... {pct}%")
                sys.stdout.flush()

        if part.new_partition_info.hash:
            if h.digest() != part.new_partition_info.hash:
                print(f"\r  {part.partition_name} - HASH MISMATCH!")
                return

    print(f"\r  {part.partition_name} ({num_ops} ops)... done")


def parse_payload(payload_file: io.BufferedReader):
    """Parse payload.bin header and return manifest and data offset."""
    magic = payload_file.read(4)
    if magic != PAYLOAD_MAGIC:
        raise ValueError(f"Invalid payload magic: expected {PAYLOAD_MAGIC!r}, got {magic!r}")

    file_format_version = u64(payload_file.read(8))
    if file_format_version not in (1, 2):
        raise ValueError(f"Unsupported payload version: {file_format_version}")

    manifest_size = u64(payload_file.read(8))

    metadata_signature_size = 0
    if file_format_version > 1:
        metadata_signature_size = u32(payload_file.read(4))

    manifest = payload_file.read(manifest_size)
    payload_file.read(metadata_signature_size)  # skip metadata signature

    data_offset = payload_file.tell()

    dam = um.DeltaArchiveManifest()
    dam.ParseFromString(manifest)

    return dam, data_offset


def list_partitions(dam) -> None:
    """Print partition info without extracting."""
    print(f"{'Partition':<30} {'Operations':>10}  Types")
    print("-" * 70)
    for part in dam.partitions:
        op_types = set()
        for op in part.operations:
            op_types.add(OP_NAMES.get(op.type, f"UNKNOWN({op.type})"))
        print(f"  {part.partition_name:<28} {len(part.operations):>10}  {', '.join(sorted(op_types))}")
    print(f"\nTotal: {len(dam.partitions)} partitions")


def main():
    parser = argparse.ArgumentParser(
        description="Extract partition images from Android OTA payload.bin files."
    )
    parser.add_argument("payload", help="Path to payload.bin file")
    parser.add_argument("-o", "--output", default="output",
                        help="Output directory (default: ./output)")
    parser.add_argument("-p", "--partitions", nargs="+",
                        help="Extract only specified partitions (e.g., boot system vendor)")
    parser.add_argument("-l", "--list", action="store_true",
                        help="List partitions without extracting")
    args = parser.parse_args()

    if not os.path.isfile(args.payload):
        print(f"Error: {args.payload} not found", file=sys.stderr)
        sys.exit(1)

    if not HAS_BROTLI:
        print("Warning: 'brotli' package not installed. Brotli-compressed payloads will fail.")
        print("  Install with: pip install brotli\n")

    with open(args.payload, "rb") as payload_file:
        dam, data_offset = parse_payload(payload_file)
        print(f"Payload version: {dam.minor_version}, "
              f"block size: {dam.block_size}, "
              f"partitions: {len(dam.partitions)}")

        if args.list:
            list_partitions(dam)
            return

        # Filter partitions if requested
        partitions = dam.partitions
        if args.partitions:
            requested = set(p.lower() for p in args.partitions)
            partitions = [p for p in dam.partitions if p.partition_name.lower() in requested]
            not_found = requested - {p.partition_name.lower() for p in partitions}
            if not_found:
                print(f"Warning: partitions not found: {', '.join(sorted(not_found))}")

        if not partitions:
            print("No partitions to extract.")
            return

        os.makedirs(args.output, exist_ok=True)
        print(f"Extracting {len(partitions)} partitions to {args.output}/\n")

        # Validate operations
        supported_ops = {OP_REPLACE, OP_REPLACE_BZ, OP_REPLACE_XZ, OP_ZERO, OP_DISCARD}
        for part in partitions:
            for op in part.operations:
                if op.type not in supported_ops:
                    op_name = OP_NAMES.get(op.type, f"UNKNOWN({op.type})")
                    print(f"Error: partition '{part.partition_name}' uses unsupported "
                          f"operation '{op_name}'.")
                    print("This is likely an incremental (delta) OTA. "
                          "Only full OTA payloads are supported.")
                    sys.exit(1)

        for part in partitions:
            dump_part(payload_file, data_offset, part, args.output)

    print(f"\nDone! Extracted to {args.output}/")


if __name__ == "__main__":
    main()
