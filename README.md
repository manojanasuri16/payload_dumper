# android-payload-dumper

A Python tool to extract partition images (`.img`) from Android OTA firmware `payload.bin` files — locally, **from a URL without a full download**, or straight from an OTA `.zip` without unpacking it first.

Android OTA updates ship as a `payload.bin` inside a firmware ZIP. That file contains compressed partition images (system, boot, vendor, etc.) packed in Google's Chrome OS update format (`CrAU`). This tool parses the payload, decompresses each partition **in parallel**, verifies integrity via SHA-256 hashes, and writes individual `.img` files that can be flashed or mounted.

**Highlights:**
- **Stream from a URL** with HTTP range requests — extract `boot.img` out of a 4 GB OTA without downloading the whole thing.
- **Read straight from OTA `.zip`** — no `unzip` step required; operates on the stored `payload.bin` member in place.
- **Parallel extraction** across partitions with a live per-partition progress UI.
- **`--json` output** for scripting and CI integration.

## Prerequisites

- Python 3.9 or higher
- One of: [pipx](https://pipx.pypa.io/), [uv](https://docs.astral.sh/uv/), or pip

## Installation

### As a global CLI (recommended)

```bash
pipx install android-payload-dumper
```

This puts `payload-dumper` on your PATH so you can run it from anywhere without activating a venv.

<details>
<summary>Install from this repo (for development)</summary>

```bash
git clone https://github.com/manojanasuri16/payload_dumper.git
cd payload_dumper
uv sync
```

Then run via `uv run payload-dumper …` (shown in examples below).

Pip alternative:

```bash
git clone https://github.com/manojanasuri16/payload_dumper.git
cd payload_dumper
pip install -e .
```
</details>

<details>
<summary>Install straight from GitHub with pipx</summary>

```bash
pipx install git+https://github.com/manojanasuri16/payload_dumper.git
```

Useful if you want the latest `main` without waiting for a PyPI release.
</details>

## Getting payload.bin

1. Download the full OTA firmware ZIP for your device (from the manufacturer's website, or sites like [Full OTA Images for Pixel](https://developers.google.com/android/ota))
2. Extract the ZIP — you'll find `payload.bin` inside
3. Copy `payload.bin` into the `payload_dumper` directory (or provide the full path)

## Usage

> Examples below assume you installed with `pipx` and `payload-dumper` is on your PATH. If you're running from a clone, prefix every command with `uv run` (e.g. `uv run payload-dumper payload.bin`).

### Extract all partitions

```bash
payload-dumper payload.bin             # local payload.bin
payload-dumper firmware_ota.zip        # reads payload.bin out of the OTA zip
payload-dumper https://dl.example.com/device-ota.zip   # streams from URL
```

All `.img` files are saved to the `output/` directory. Partitions are extracted in parallel (up to 8 workers by default), with a live progress bar per partition and an overall total.

For URLs, the server must support HTTP range requests (nearly every OEM mirror and CDN does) — only the exact bytes needed per operation are fetched, so extracting just `boot.img` from a 4 GB remote OTA downloads on the order of 100 MB.

### Extract specific partitions only

```bash
payload-dumper payload.bin -p boot system vendor
```

### Custom output directory

```bash
payload-dumper payload.bin -o extracted/
```

### Control parallelism

```bash
payload-dumper payload.bin -j 4     # 4 workers
payload-dumper payload.bin -j 1     # serial extraction
```

### List partitions without extracting

```bash
payload-dumper payload.bin -l
payload-dumper payload.bin -l --json    # machine-readable output for scripts/CI
```

Example output:

```
payload  minor_version=0  block_size=4096  partitions=35

  35 partitions
  ┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━┳━━━━━━━━━━━━━━━━━━━┓
  ┃ Partition                  ┃ Ops ┃ Types             ┃
  ┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━╇━━━━━━━━━━━━━━━━━━━┩
  │ boot                       │  85 │ REPLACE_XZ        │
  │ dtbo                       │  12 │ REPLACE_XZ        │
  │ modem                      │  86 │ REPLACE_XZ        │
  │ system                     │ 384 │ REPLACE_XZ, ZERO  │
  │ vendor                     │ 205 │ REPLACE_XZ        │
  │ …                          │   … │ …                 │
  └────────────────────────────┴─────┴───────────────────┘
```

## Command Reference

```
payload-dumper [-h] [-o OUTPUT] [-p NAME ...] [-l] [--json] [-j WORKERS] [-V] payload
```

| Option | Description |
|---|---|
| `payload` | Path or URL to `payload.bin` or an OTA `.zip` containing it (required) |
| `-o`, `--output DIR` | Output directory for extracted images (default: `output/`) |
| `-p`, `--partitions NAME [NAME ...]` | Extract only the listed partitions |
| `-l`, `--list` | List all partitions in the payload and exit |
| `--json` | With `--list`, emit JSON to stdout instead of a table |
| `-j`, `--workers N` | Parallel extraction workers (default: `min(8, cpu_count)`) |
| `-V`, `--version` | Print version and exit |
| `-h`, `--help` | Show help message |

Also runnable as a module: `python -m payload_dumper payload.bin`.

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
3. **Operation validation** — Checks that all operations are supported up-front (full OTA only — no delta/incremental ops like `SOURCE_COPY` or `BSDIFF`) so a failure doesn't leave half-written images on disk
4. **Parallel extraction** — Each partition is handed to a worker thread that opens its own file handle, iterates the ops in order, decompresses and writes, and updates a shared Rich progress bar
5. **Integrity verification** — Each operation's compressed bytes are verified against its SHA-256 hash, and the final partition image is verified against the expected partition hash

## Payload Binary Format

```
+---------------------+
| Magic: "CrAU"       |  4 bytes
| Format Version      |  8 bytes (uint64, big-endian)
| Manifest Size       |  8 bytes (uint64, big-endian)
| Metadata Sig Size   |  4 bytes (uint32, big-endian, v2 only)
| Manifest (protobuf) |  <manifest_size> bytes
| Metadata Signature  |  <metadata_sig_size> bytes
| Data Blobs          |  Remaining bytes (compressed partition data)
+---------------------+
```

## Limitations

- **Full OTA only** — Incremental/delta OTA payloads (containing `SOURCE_COPY`, `SOURCE_BSDIFF`, `PUFFDIFF`, `BROTLI_BSDIFF`, etc.) are not supported. These require the previous version's partition images to apply diffs against.
- **No streaming** — The entire payload file must be accessible on disk (no stdin/pipe support).

## Troubleshooting

### `ModuleNotFoundError: No module named 'google'` or `'rich'`

Install (or reinstall) the package so its dependencies come with it:

```bash
pipx install android-payload-dumper   # or: pipx upgrade android-payload-dumper
uv sync                               # if working from a clone
pip install -e .                      # pip-from-clone alternative
```

### `_lzma.LZMAError: Input format not supported by decoder`

This was a bug in older versions. The current version automatically tries multiple LZMA formats (XZ container, LZMA alone, raw LZMA2) as fallbacks. Make sure you're using the latest version.

### `unsupported op` error

Your payload is an incremental (delta) OTA, not a full OTA. This tool only supports full OTA payloads. Download the full OTA image for your device instead.

## Regenerating Protobuf Bindings

If you modify `update_metadata.proto`, regenerate the Python bindings into the package:

```bash
uv add --dev grpcio-tools
uv run python -m grpc_tools.protoc \
    --python_out=src/payload_dumper \
    --proto_path=. \
    update_metadata.proto
```

## License

MIT
