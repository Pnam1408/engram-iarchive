import base64
import json
from typing import Any

from .errors import SerializationError


def serialize(value: Any) -> bytes:
    """Serialize a JSON-compatible value into deterministic UTF-8 bytes."""
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return text.encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SerializationError(f"Cannot serialize metadata value: {exc}") from exc


def deserialize(data: bytes) -> Any:
    """Deserialize UTF-8 JSON bytes."""
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SerializationError(f"Cannot deserialize metadata value: {exc}") from exc


def encode_bytes(value: bytes) -> str:
    if not isinstance(value, bytes):
        raise TypeError("value must be bytes")
    return base64.b64encode(value).decode("ascii")


def decode_bytes(value: Any, field: str) -> bytes:
    if not isinstance(value, str):
        raise SerializationError(f"{field} must be a base64 string")
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise SerializationError(f"{field} contains invalid base64") from exc
