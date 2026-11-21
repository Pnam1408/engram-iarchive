from uuid import UUID
import pytest

from engram.commitment.common.encoding import (
    encode_bytes, encode_list, encode_optional, encode_raw_bytes, encode_string,
    encode_u8, encode_u16, encode_u32, encode_u64, encode_uuid,
)
from engram.commitment.common.exceptions import InvalidTypeError, ValueOutOfRangeError



@pytest.mark.parametrize("fn,width,value", [
    (encode_u8, 1, 0xAB), (encode_u16, 2, 0xABCD),
    (encode_u32, 4, 0x01020304), (encode_u64, 8, 0x0102030405060708),
])
def test_integer_big_endian(fn, width, value):
    assert fn(value) == value.to_bytes(width, "big")



@pytest.mark.parametrize("fn", [encode_u8, encode_u16, encode_u32, encode_u64])
def test_integer_rejects_bool_and_wrong_type(fn):
    with pytest.raises(InvalidTypeError):
        fn(True)
    with pytest.raises(InvalidTypeError):
        fn(1.0)



@pytest.mark.parametrize("fn,bits", [
    (encode_u8, 8), (encode_u16, 16), (encode_u32, 32), (encode_u64, 64),
])
def test_integer_rejects_negative_and_overflow(fn, bits):
    with pytest.raises(ValueOutOfRangeError):
        fn(-1)
    with pytest.raises(ValueOutOfRangeError):
        fn(1 << bits)


def test_bytes_are_length_prefixed_and_raw_helper_is_identity():
    assert encode_bytes(b"abc") == b"\x00\x00\x00\x03abc"
    assert encode_bytes(b"") == b"\x00\x00\x00\x00"
    assert encode_raw_bytes(b"abc") == b"abc"


def test_string_utf8_length_prefix():
    assert encode_string("é") == b"\x00\x00\x00\x02\xc3\xa9"
    assert encode_string("") == b"\x00\x00\x00\x00"
    assert encode_string("  AbC  ") != encode_string("abc")


def test_uuid_encoding_is_16_network_order_bytes():
    value = UUID("00112233-4455-6677-8899-aabbccddeeff")
    assert encode_uuid(value) == bytes.fromhex("00112233445566778899aabbccddeeff")


def test_optional_presence_marker_distinguishes_none_from_empty_and_zero():
    assert encode_optional(None, encode_u8) == b"\x00"
    assert encode_optional(0, encode_u8) == b"\x01\x00"
    assert encode_optional(b"", encode_bytes) == b"\x01\x00\x00\x00\x00"


def test_list_preserves_input_order_and_count():
    assert encode_list([1, 2, 3], encode_u8) == b"\x00\x00\x00\x03\x01\x02\x03"
    assert encode_list([], encode_u8) == b"\x00\x00\x00\x00"
