from uuid import UUID

from fractional_indexing import (
    validate_order_key as _validate_order_key,
)

HASH_SIZE = 32


def validate_non_empty_string(value: str, field_name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")

    if not value:
        raise ValueError(f"{field_name} must not be empty")


def validate_uuid(value: str, field_name: str) -> None:
    validate_non_empty_string(value, field_name)

    try:
        parsed = UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"{field_name} must be a valid UUID") from exc

    if parsed.version != 4:
        raise ValueError(f"{field_name} must be a UUIDv4")


def validate_timestamp(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")

    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")


def validate_positive_integer(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")

    if value <= 0:
        raise ValueError(f"{field_name} must be positive")


def validate_non_negative_integer(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")

    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")


def validate_hash(value: bytes, field_name: str) -> None:
    if not isinstance(value, bytes):
        raise TypeError(f"{field_name} must be bytes")

    if len(value) != HASH_SIZE:
        raise ValueError(f"{field_name} must contain exactly {HASH_SIZE} bytes")


def validate_non_empty_bytes(value: bytes, field_name: str) -> None:
    if not isinstance(value, bytes):
        raise TypeError(f"{field_name} must be bytes")

    if not value:
        raise ValueError(f"{field_name} must not be empty")


def validate_order_key(value: str) -> None:
    validate_non_empty_string(value, "order_key")

    try:
        result = _validate_order_key(value)
    except Exception as exc:
        raise ValueError("Invalid fractional OrderKey") from exc

    if result is False:
        raise ValueError("Invalid fractional OrderKey")
