# payload_dumper

Extract partition images from Android OTA `payload.bin` files.

## Quick Start

```bash
git clone https://github.com/manojanasuri16/payload_dumper.git
cd payload_dumper
uv sync
```

Extract a firmware's `payload.bin`:

```bash
uv run python payload_dumper.py payload.bin
```

Images are saved to `output/` by default.

## Usage

```
payload_dumper.py [-h] [-o OUTPUT] [-p PARTITIONS ...] [-l] payload.bin
```

| Option | Description |
|---|---|
| `-o DIR` | Output directory (default: `output/`) |
| `-p NAME [NAME ...]` | Extract only specific partitions |
| `-l` | List partitions without extracting |

### Examples

```bash
# Extract only boot and system
uv run python payload_dumper.py payload.bin -p boot system

# List all partitions in a payload
uv run python payload_dumper.py payload.bin -l

# Extract to a custom directory
uv run python payload_dumper.py payload.bin -o my_images/
```

## Without uv

```bash
pip install protobuf brotli
python payload_dumper.py payload.bin
```

## Supported Operations

Full OTA payloads only: `REPLACE`, `REPLACE_BZ`, `REPLACE_XZ`, `ZERO`, `DISCARD`.

Incremental/delta OTAs are not supported.

## License

MIT
