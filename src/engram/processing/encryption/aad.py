"""Canonical, versioned AAD encoding for encrypted chunks."""

from __future__ import annotations

import struct

from engram.processing.exceptions import InvalidConfigurationError

AAD_VERSION = 2
_DOMAIN = b"ENGRAM-CHUNK-AAD\x00"
_U16 = struct.Struct(">H")
_U32 = struct.Struct(">I")
_U64 = struct.Struct(">Q")


def _text_field(name: str, value: str, width: struct.Struct) -> bytes:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    try:
        raw = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{name} must be valid UTF-8") from exc
    try:
        prefix = width.pack(len(raw))
    except struct.error as exc:
        raise ValueError(f"{name} is too long") from exc
    return prefix + raw


def build_aad(
    *,
    object_id: str,
    chunk_id: str,
    algorithm: str,
    key_id: str,
    plaintext_size: int,
    version: int = AAD_VERSION,
) -> bytes:
    """Build unambiguous AAD binding identity and encryption metadata."""
    if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
        raise InvalidConfigurationError("AAD version must be a positive integer")
    if version != AAD_VERSION:
        raise InvalidConfigurationError(f"Unsupported AAD version: {version}")
    if (
        isinstance(plaintext_size, bool)
        or not isinstance(plaintext_size, int)
        or plaintext_size <= 0
    ):
        raise ValueError("plaintext_size must be a positive integer")
    try:
        size_bytes = _U64.pack(plaintext_size)
        version_bytes = _U16.pack(version)
    except struct.error as exc:
        raise ValueError("AAD integer field exceeds its encoded range") from exc

    return b"".join(
        (
            _DOMAIN,
            version_bytes,
            _text_field("object_id", object_id, _U32),
            _text_field("chunk_id", chunk_id, _U32),
            _text_field("algorithm", algorithm, _U16),
            _text_field("key_id", key_id, _U16),
            size_bytes,
        )
    )
