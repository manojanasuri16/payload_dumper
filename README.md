# payload_dumper

Extract partition images (`.img`) from Android OTA `payload.bin` files.

Supports full OTA payloads with `REPLACE`, `REPLACE_BZ` (bzip2), `REPLACE_XZ` (LZMA/XZ), `ZERO`, and `DISCARD` operations.

## Prerequisites

- Python 3.8+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

## Installation

### Using uv (recommended)

```bash
git clone https://github.com/manojanasuri16/payload_dumper.git
cd payload_dumper
uv sync
```

### Using pip

```bash
git clone https://github.com/manojanasuri16/payload_dumper.git
cd payload_dumper
pip install -r requirements.txt
```

## Usage

### Extract all partitions

```bash
# With uv
uv run python payload_dumper.py payload.bin

# With pip
python payload_dumper.py payload.bin
```

Extracted `.img` files will be saved to the `output/` directory.

### Extract specific partitions

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

## Options

| Flag | Description |
|---|---|
| `-o`, `--output` | Output directory (default: `./output`) |
| `-p`, `--partitions` | Extract only specified partitions |
| `-l`, `--list` | List partitions and their operation types |
| `-h`, `--help` | Show help message |

## How it works

1. Parses the `payload.bin` header (validates `CrAU` magic, reads manifest)
2. Deserializes the protobuf manifest to get partition metadata
3. For each partition, reads compressed data, decompresses it, verifies SHA-256 hashes, and writes the output `.img` file

## Limitations

- Only supports **full OTA** payloads. Incremental/delta OTAs (containing `SOURCE_COPY`, `SOURCE_BSDIFF`, `PUFFDIFF`, etc.) are not supported.

## Regenerating protobuf bindings

If you need to update the protobuf bindings after modifying `update_metadata.proto`:

```bash
uv add --dev grpcio-tools
uv run python -m grpc_tools.protoc --python_out=. --proto_path=. update_metadata.proto
```

## License

MIT
