from uuid import uuid4
import pytest

from engram.commitment.common.validation import (
    require_bytes,
    require_string,
    require_uuid,
    require_uint,
    require_length,
    require_not_none,
    require_unique,
)
from engram.commitment.common.exceptions import (
    DuplicateValueError,
    InvalidInputError,
    InvalidTypeError,
    ValueOutOfRangeError,
)


def test_type_validators():
    assert require_bytes(b"x") == b"x"
    assert require_string("x") == "x"
    assert require_uuid(uuid4())
    assert require_not_none(0) == 0
    with pytest.raises(InvalidTypeError):
        require_bytes(bytearray(b"x"))
    with pytest.raises(InvalidTypeError):
        require_string(1)
    with pytest.raises(InvalidTypeError):
        require_uuid("not a UUID")
    with pytest.raises(InvalidInputError):
        require_not_none(None)
    with pytest.raises(InvalidInputError):
        require_string("", allow_empty=False)


def test_uint_validation():
    assert require_uint(255, 8) == 255
    with pytest.raises(InvalidTypeError):
        require_uint(True, 8)
    with pytest.raises(ValueOutOfRangeError):
        require_uint(256, 8)
    with pytest.raises(ValueOutOfRangeError):
        require_uint(-1, 8)
    with pytest.raises(InvalidInputError):
        require_uint(1, 7)


def test_length_and_uniqueness():
    assert require_length(b"abc", 1, 3) == b"abc"
    with pytest.raises(ValueOutOfRangeError):
        require_length(b"", 1, 3)
    require_unique([1, 2, 3])
    with pytest.raises(DuplicateValueError):
        require_unique([1, 2, 1])
    require_unique([{"id": 1}, {"id": 2}], key=lambda x: x["id"])
    with pytest.raises(DuplicateValueError):
        require_unique([{"id": 1}, {"id": 1}], key=lambda x: x["id"])


def test_field_element_validation():
    from engram.commitment.common.validation import require_field_element

    assert require_field_element(0, 17) == 0
    assert require_field_element(16, 17) == 16
    with pytest.raises(InvalidTypeError):
        require_field_element(True, 17)
    with pytest.raises(ValueOutOfRangeError):
        require_field_element(-1, 17)
    with pytest.raises(ValueOutOfRangeError):
        require_field_element(17, 17)
    with pytest.raises(InvalidInputError):
        require_field_element(1, 1)
