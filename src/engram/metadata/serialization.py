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
