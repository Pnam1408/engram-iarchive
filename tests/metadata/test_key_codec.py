import pytest

from engram.metadata import KeyCodec


def test_encode_decode_bytes():
    original = "object-001"

    encoded = KeyCodec.encode_bytes(original)
    decoded, offset = KeyCodec.decode_bytes(encoded)

    assert decoded == original.encode("utf-8")
    assert offset == len(encoded)


def test_encode_decode_uint64():
    for value in [0, 1, 255, 65536, 2**32, 2**64 - 1]:
        encoded = KeyCodec.encode_uint64(value)
        decoded, offset = KeyCodec.decode_uint64(encoded)

        assert decoded == value
        assert offset == 8


def test_uint64_lexicographical_order():
    values = [1, 10, 2, 100, 0]
    encoded = [KeyCodec.encode_uint64(v) for v in values]

    decoded_sorted = [KeyCodec.decode_uint64(item)[0] for item in sorted(encoded)]

    assert decoded_sorted == sorted(values)


def test_reject_negative_uint64():
    with pytest.raises(ValueError):
        KeyCodec.encode_uint64(-1)


def test_reject_incomplete_bytes():
    with pytest.raises(Exception):
        KeyCodec.decode_bytes(b"\x00\x00")
