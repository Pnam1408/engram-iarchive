"""Small, dependency-free validation helpers for Commitment primitives."""

from collections.abc import Iterable, Sized
from typing import Any, Callable, TypeVar
from uuid import UUID


from .exceptions import (
    DuplicateValueError,
    InvalidInputError,
    InvalidTypeError,
    ValueOutOfRangeError,
)

T = TypeVar("T")


def require_bytes(value: Any, *, name: str = "value") -> bytes:
    if type(value) is not bytes:
        raise InvalidTypeError(f"{name} must be bytes")
    return value


def require_string(value: Any, *, name: str = "value", allow_empty: bool = True) -> str:
    if type(value) is not str:
        raise InvalidTypeError(f"{name} must be str")
    if not allow_empty and not value:
        raise InvalidInputError(f"{name} must not be empty")
    return value


def require_uuid(value: Any, *, name: str = "value") -> UUID:
    if not isinstance(value, UUID):
        raise InvalidTypeError(f"{name} must be uuid.UUID")
    return value


def require_uint(value: Any, bits: int, *, name: str = "value") -> int:
    if type(value) is not int:
        raise InvalidTypeError(f"{name} must be an int (bool is not accepted)")
    if bits not in (8, 16, 32, 64):
        raise InvalidInputError("bits must be one of 8, 16, 32, or 64")
    if value < 0 or value > (1 << bits) - 1:
        raise ValueOutOfRangeError(f"{name} must fit in an unsigned {bits}-bit integer")
    return value


def require_length(
    value: Sized, minimum: int, maximum: int, *, name: str = "value"
) -> Sized:
    if not isinstance(value, Sized):
        raise InvalidTypeError(f"{name} must be a sized value")
    if (
        type(minimum) is not int
        or type(maximum) is not int
        or minimum < 0
        or maximum < minimum
    ):
        raise InvalidInputError("invalid length bounds")
    length = len(value)
    if not minimum <= length <= maximum:
        raise ValueOutOfRangeError(
            f"{name} length must be between {minimum} and {maximum}"
        )
    return value


def require_not_none(value: Any, *, name: str = "value") -> Any:
    if value is None:
        raise InvalidInputError(f"{name} must not be None")
    return value


def require_unique(
    values: Iterable[T], *, key: Callable[[T], Any] | None = None, name: str = "values"
) -> None:
    seen: set[Any] = set()
    for item in values:
        marker = key(item) if key is not None else item
        try:
            duplicate = marker in seen
            if not duplicate:
                seen.add(marker)
        except TypeError as exc:
            raise InvalidTypeError(f"{name} uniqueness key must be hashable") from exc
        if duplicate:
            raise DuplicateValueError(f"duplicate value in {name}: {marker!r}")


def require_field_element(value: Any, modulus: int, *, name: str = "value") -> int:
    """Validate an integer in the canonical range of a prime field.

    No coercion or modular reduction is performed; bool is deliberately rejected.
    """
    if type(modulus) is not int or modulus <= 1:
        raise InvalidInputError("modulus must be an int greater than 1")
    if type(value) is not int:
        raise InvalidTypeError(f"{name} must be an int (bool is not accepted)")
    if value < 0 or value >= modulus:
        raise ValueOutOfRangeError(f"{name} must satisfy 0 <= {name} < modulus")
    return value
