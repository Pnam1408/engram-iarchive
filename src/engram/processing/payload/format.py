"""Versioned binary record format for generation payloads.

V1 record:
    magic[4] | format_version[u16] | header_length[u32] |
    object_id_length[u32] | object_id[bytes] |
    chunk_id_length[u32] | chunk_id[bytes] |
    algorithm_length[u16] | algorithm[bytes] |
    key_id_length[u16] | key_id[bytes] |
    aad_version[u16] | plaintext_size[u64] | plaintext_sha256[32] |
    nonce_length[u16] | nonce[bytes] |
    ciphertext_length[u64] | ciphertext[bytes]

All integers are unsigned big-endian. ``header_length`` includes bytes from
magic through the final header field, but excludes ciphertext.
"""

from __future__ import annotations

import struct

from engram.processing.exceptions import PayloadError, UnsupportedPayloadVersionError

MAGIC = b"ENGR"
PAYLOAD_FORMAT_VERSION = 1
_U16 = struct.Struct(">H")
_U32 = struct.Struct(">I")
_U64 = struct.Struct(">Q")
_FIXED_PREFIX = struct.Struct(">4sHI")
_SHA256_SIZE = 32


def _pack_length(value: int, width: struct.Struct, field: str) -> bytes:
    try:
        return width.pack(value)
    except struct.error as exc:
        raise PayloadError(f"{field} is too large for the payload format") from exc


def encode_record(chunk) -> bytes:
    """Serialize one EncryptedChunk into a self-describing binary record."""
    values = (
        ("object_id", chunk.object_id.encode("utf-8"), _U32),
        ("chunk_id", chunk.chunk_id.encode("utf-8"), _U32),
        ("algorithm", chunk.algorithm.encode("utf-8"), _U16),
        ("key_id", chunk.key_id.encode("utf-8"), _U16),
    )
    variable = bytearray()
    for name, raw, width in values:
        if not raw:
            raise PayloadError(f"{name} must not be empty")
        variable += _pack_length(len(raw), width, name)
        variable += raw

    try:
        aad_version = _U16.pack(chunk.aad_version)
        plaintext_size = _U64.pack(chunk.plaintext_size)
        nonce_length = _U16.pack(len(chunk.nonce))
        ciphertext_length = _U64.pack(len(chunk.ciphertext))
    except struct.error as exc:
        raise PayloadError("EncryptedChunk metadata exceeds format limits") from exc

    header_without_prefix = (
        bytes(variable)
        + aad_version
        + plaintext_size
        + bytes.fromhex(chunk.plaintext_sha256)
        + nonce_length
        + chunk.nonce
        + ciphertext_length
    )
    header_length = _FIXED_PREFIX.size + len(header_without_prefix)
    prefix = _FIXED_PREFIX.pack(MAGIC, PAYLOAD_FORMAT_VERSION, header_length)
    return prefix + header_without_prefix + chunk.ciphertext


def decode_record(record_bytes: bytes, *, expected_format_version: int):
    """Parse one record and return an EncryptedChunk.

    Parsing is intentionally strict: the supplied byte range must contain
    exactly one complete record, with no trailing or truncated bytes.
    """
    from engram.processing.artifacts import EncryptedChunk

    if expected_format_version != PAYLOAD_FORMAT_VERSION:
        raise UnsupportedPayloadVersionError(
            f"Unsupported payload format version: {expected_format_version}"
        )
    view = memoryview(record_bytes)
    if len(view) < _FIXED_PREFIX.size:
        raise PayloadError("Record is shorter than the fixed prefix")

    magic, version, header_length = _FIXED_PREFIX.unpack(view[: _FIXED_PREFIX.size])
    if magic != MAGIC:
        raise PayloadError("Invalid payload record magic")
    if version != expected_format_version:
        raise UnsupportedPayloadVersionError(f"Unsupported record version: {version}")
    if header_length < _FIXED_PREFIX.size or header_length > len(view):
        raise PayloadError("Invalid record header length")

    cursor = _FIXED_PREFIX.size

    def read(width: struct.Struct, field: str) -> int:
        nonlocal cursor
        if cursor + width.size > header_length:
            raise PayloadError(f"Truncated {field} length")
        value = width.unpack(view[cursor : cursor + width.size])[0]
        cursor += width.size
        return value

    def read_bytes(length: int, field: str) -> bytes:
        nonlocal cursor
        end = cursor + length
        if end > header_length:
            raise PayloadError(f"Truncated {field}")
        value = bytes(view[cursor:end])
        cursor = end
        return value

    def read_text(width: struct.Struct, field: str) -> str:
        length = read(width, field)
        if length == 0:
            raise PayloadError(f"{field} must not be empty")
        try:
            return read_bytes(length, field).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PayloadError(f"{field} is not valid UTF-8") from exc

    object_id = read_text(_U32, "object_id")
    chunk_id = read_text(_U32, "chunk_id")
    algorithm = read_text(_U16, "algorithm")
    key_id = read_text(_U16, "key_id")
    aad_version = read(_U16, "aad_version")
    plaintext_size = read(_U64, "plaintext_size")
    digest = read_bytes(_SHA256_SIZE, "plaintext_sha256").hex()
    nonce_length = read(_U16, "nonce")
    nonce = read_bytes(nonce_length, "nonce")
    ciphertext_length = read(_U64, "ciphertext")
    if cursor != header_length:
        raise PayloadError("Header length does not match encoded fields")
    if header_length + ciphertext_length != len(view):
        raise PayloadError("Ciphertext length does not match record length")

    ciphertext = bytes(view[header_length:])
    try:
        return EncryptedChunk(
            object_id=object_id,
            chunk_id=chunk_id,
            algorithm=algorithm,
            key_id=key_id,
            nonce=nonce,
            ciphertext=ciphertext,
            plaintext_size=plaintext_size,
            plaintext_sha256=digest,
            aad_version=aad_version,
        )
    except (TypeError, ValueError) as exc:
        raise PayloadError(
            "Decoded record contains invalid EncryptedChunk metadata"
        ) from exc
