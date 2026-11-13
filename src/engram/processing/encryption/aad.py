"""Canonical, versioned AAD encoding for encrypted chunks."""

from __future__ import annotations

import struct

from engram.processing.exceptions import InvalidConfigurationError

AAD_VERSION = 1
_DOMAIN = b"ENGRAM-CHUNK-AAD\x00"


def build_aad(*, object_id: str, chunk_id: str, version: int = AAD_VERSION) -> bytes:
    """Encode chunk identity unambiguously using UTF-8 length-prefixed fields."""
    if not isinstance(object_id, str) or not object_id:
        raise ValueError("object_id must be a non-empty string")
    if not isinstance(chunk_id, str) or not chunk_id:
        raise ValueError("chunk_id must be a non-empty string")
    if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
        raise InvalidConfigurationError("AAD version must be a positive integer")
    if version != AAD_VERSION:
        raise InvalidConfigurationError(f"Unsupported AAD version: {version}")

    try:
        object_bytes = object_id.encode("utf-8")
        chunk_bytes = chunk_id.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("identifiers must be valid UTF-8 strings") from exc

    if len(object_bytes) > 0xFFFFFFFF or len(chunk_bytes) > 0xFFFFFFFF:
        raise ValueError("identifier is too long")

    return b"".join(
        (
            _DOMAIN,
            struct.pack(">H", version),
            struct.pack(">I", len(object_bytes)),
            object_bytes,
            struct.pack(">I", len(chunk_bytes)),
            chunk_bytes,
        )
    )
