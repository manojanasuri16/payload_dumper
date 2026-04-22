"""Extract partition images from Android OTA payload.bin files."""

from .core import (
    Payload,
    PayloadError,
    UnsupportedPayloadError,
    extract_partition,
    extract_partition_to_stream,
    parse_payload,
    validate_operations,
)
from .source import (
    ByteSource,
    FileSource,
    HttpSource,
    SourceError,
    ZipMemberSource,
    open_source,
)

__version__ = "2.3.0"

__all__ = [
    "ByteSource",
    "FileSource",
    "HttpSource",
    "Payload",
    "PayloadError",
    "SourceError",
    "UnsupportedPayloadError",
    "ZipMemberSource",
    "extract_partition",
    "extract_partition_to_stream",
    "open_source",
    "parse_payload",
    "validate_operations",
    "__version__",
]
