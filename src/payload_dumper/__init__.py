"""Extract partition images from Android OTA payload.bin files."""

from .core import (
    Payload,
    PayloadError,
    UnsupportedPayloadError,
    extract_partition,
    parse_payload,
    validate_operations,
)

__version__ = "2.1.0"

__all__ = [
    "Payload",
    "PayloadError",
    "UnsupportedPayloadError",
    "extract_partition",
    "parse_payload",
    "validate_operations",
    "__version__",
]
