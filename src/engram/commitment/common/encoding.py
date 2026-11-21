"""Deterministic primitive binary encodings for ENGRAM commitments."""

from collections.abc import Iterable, Callable
from typing import TypeVar
from uuid import UUID


from .exceptions import InvalidEncodingValueError, ValueOutOfRangeError
from .validation import require_bytes, require_string, require_uint, require_uuid

MAX_U32 = (1 << 32) - 1
T = TypeVar("T")


def encode_u8(value: int) -> bytes:
    return require_uint(value, 8).to_bytes(1, "big")


def encode_u16(value: int) -> bytes:
    return require_uint(value, 16).to_bytes(2, "big")


def encode_u32(value: int) -> bytes:
    return require_uint(value, 32).to_bytes(4, "big")


def encode_u64(value: int) -> bytes:
    return require_uint(value, 64).to_bytes(8, "big")


def encode_raw_bytes(value: bytes) -> bytes:
    """Return bytes unchanged (no length prefix)."""
    return require_bytes(value)


def encode_bytes(value: bytes) -> bytes:
    """Encode bytes as u32 length followed by raw bytes."""
    raw = require_bytes(value)
    if len(raw) > MAX_U32:
        raise ValueOutOfRangeError("bytes length exceeds u32 maximum")
    return encode_u32(len(raw)) + raw


def encode_string(value: str) -> bytes:
    """Encode a string as u32 UTF-8 byte length followed by strict UTF-8."""
    text = require_string(value)
    raw = text.encode("utf-8", errors="strict")
    if len(raw) > MAX_U32:
        raise ValueOutOfRangeError("UTF-8 string length exceeds u32 maximum")
    return encode_u32(len(raw)) + raw


def encode_uuid(value: UUID) -> bytes:
    return require_uuid(value).bytes


def encode_optional(value: T | None, encoder: Callable[[T], bytes]) -> bytes:
    if value is None:
        return b"\x00"
    encoded = encoder(value)
    if type(encoded) is not bytes:
        raise InvalidEncodingValueError("optional encoder must return bytes")
    return b"\x01" + encoded


def encode_list(values: Iterable[T], encoder: Callable[[T], bytes]) -> bytes:
    """Encode a materialized list with u32 count and ordered element encodings."""
    items = tuple(values)
    if len(items) > MAX_U32:
        raise ValueOutOfRangeError("list count exceeds u32 maximum")
    parts = [encode_u32(len(items))]
    for item in items:
        encoded = encoder(item)
        if type(encoded) is not bytes:
            raise InvalidEncodingValueError("list element encoder must return bytes")
        parts.append(encoded)
    return b"".join(parts)
