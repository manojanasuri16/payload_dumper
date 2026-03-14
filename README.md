# payload_dumper

A Python tool to extract partition images (`.img`) from Android OTA firmware `payload.bin` files.

Android OTA updates ship as a `payload.bin` file inside the firmware ZIP. This file contains compressed partition images (system, boot, vendor, etc.) packed in Google's Chrome OS update format (`CrAU`). This tool parses the payload, decompresses each partition, verifies integrity via SHA-256 hashes, and writes individual `.img` files that can be flashed or mounted.

## Prerequisites

- Python 3.8 or higher
- [uv](https://docs.astral.sh/uv/) package manager (recommended) or pip

## Installation

```bash
git clone https://github.com/manojanasuri16/payload_dumper.git
cd payload_dumper
uv sync
```

<details>
<summary>Using pip instead of uv</summary>

```bash
git clone https://github.com/manojanasuri16/payload_dumper.git
cd payload_dumper
pip install protobuf brotli
```
</details>

## Getting payload.bin

1. Download the full OTA firmware ZIP for your device (from the manufacturer's website, or sites like [Full OTA Images for Pixel](https://developers.google.com/android/ota))
2. Extract the ZIP — you'll find `payload.bin` inside
3. Copy `payload.bin` into the `payload_dumper` directory (or provide the full path)

## Usage

### Extract all partitions

```bash
uv run python payload_dumper.py payload.bin
```

All `.img` files will be saved to the `output/` directory.

### Extract specific partitions only

```bash
uv run python payload_dumper.py payload.bin -p boot system vendor
```

### Custom output directory

```bash
uv run python payload_dumper.py payload.bin -o extracted/
```

### List partitions without extracting

```bash
uv run python payload_dumper.py payload.bin -l
```

Example output:

```
Payload version: 0, block size: 4096, partitions: 35
Partition                       Operations  Types
----------------------------------------------------------------------
  boot                                  85  REPLACE_XZ
  dtbo                                  12  REPLACE_XZ
  modem                                 86  REPLACE_XZ
  system                               384  REPLACE_XZ, ZERO
  vendor                               205  REPLACE_XZ
  ...

Total: 35 partitions
```

## Command Reference

```
payload_dumper.py [-h] [-o OUTPUT] [-p PARTITIONS ...] [-l] payload
```

| Option | Description |
|---|---|
| `payload` | Path to the `payload.bin` file (required) |
| `-o`, `--output DIR` | Output directory for extracted images (default: `output/`) |
| `-p`, `--partitions NAME [NAME ...]` | Extract only the listed partitions |
| `-l`, `--list` | List all partitions in the payload and exit |
| `-h`, `--help` | Show help message |

## Supported Compression Formats

| Operation | Compression | Description |
|---|---|---|
| `REPLACE` | None | Raw uncompressed data |
| `REPLACE_BZ` | bzip2 | Bzip2 compressed data |
| `REPLACE_XZ` | XZ / LZMA | XZ or raw LZMA compressed data (with automatic format detection) |
| `ZERO` | None | Zero-filled blocks |
| `DISCARD` | None | Discarded/trimmed blocks |

## How It Works

1. **Header parsing** — Reads and validates the `CrAU` magic bytes, payload format version (v1/v2), manifest size, and metadata signature
2. **Manifest deserialization** — Uses Protocol Buffers to decode the `DeltaArchiveManifest`, which describes all partitions, their operations, and hash info
3. **Operation validation** — Checks that all operations are supported (full OTA only — no delta/incremental ops like `SOURCE_COPY` or `BSDIFF`)
4. **Extraction** — For each partition, iterates over its operations, reads the compressed data from the payload, decompresses it, and writes the result to `<partition_name>.img`
5. **Integrity verification** — Each operation's data is verified against its SHA-256 hash, and the final partition image is verified against the expected partition hash

## Payload Binary Format

```
+-------------------+
| Magic: "CrAU"     |  4 bytes
| Format Version    |  8 bytes (uint64, big-endian)
| Manifest Size     |  8 bytes (uint64, big-endian)
| Metadata Sig Size |  4 bytes (uint32, big-endian, v2 only)
| Manifest (protobuf)|  <manifest_size> bytes
| Metadata Signature |  <metadata_sig_size> bytes
| Data Blobs        |  Remaining bytes (compressed partition data)
+-------------------+
```

## Limitations

- **Full OTA only** — Incremental/delta OTA payloads (containing `SOURCE_COPY`, `SOURCE_BSDIFF`, `PUFFDIFF`, `BROTLI_BSDIFF`, etc.) are not supported. These require the previous version's partition images to apply diffs against.
- **No streaming** — The entire payload file must be accessible on disk (no stdin/pipe support).

## Troubleshooting

### `ModuleNotFoundError: No module named 'google'`

Install the protobuf package:

```bash
uv sync          # if using uv
pip install protobuf  # if using pip
```

### `_lzma.LZMAError: Input format not supported by decoder`

This was a bug in older versions. The current version automatically tries multiple LZMA formats (XZ container, LZMA alone, raw LZMA2) as fallbacks. Make sure you're using the latest version.

### `unsupported op` / `AssertionError`

Your payload is an incremental (delta) OTA, not a full OTA. This tool only supports full OTA payloads. Download the full OTA image for your device instead.

## Regenerating Protobuf Bindings

If you modify `update_metadata.proto`, regenerate the Python bindings:

```bash
uv add --dev grpcio-tools
uv run python -m grpc_tools.protoc --python_out=. --proto_path=. update_metadata.proto
```

## License

MIT
