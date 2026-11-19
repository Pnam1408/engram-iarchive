import pytest

from engram.metadata.database.errors import KeyCodecError
from engram.metadata.database import KeyCodec


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


def test_string_round_trip():
    value = "object-001"

    encoded = KeyCodec.encode_string(value)
    decoded, offset = KeyCodec.decode_string(encoded)

    assert decoded == value
    assert offset == len(encoded)


def test_string_rejects_empty_value():
    with pytest.raises(ValueError):
        KeyCodec.encode_string("")


def test_uint64_rejects_bool():
    with pytest.raises(TypeError):
        KeyCodec.encode_uint64(True)


def test_hash_round_trip():
    value = bytes(range(32))

    encoded = KeyCodec.encode_hash(value)
    decoded, offset = KeyCodec.decode_hash(encoded)

    assert decoded == value
    assert offset == 32


def test_hash_rejects_invalid_length():
    with pytest.raises(ValueError):
        KeyCodec.encode_hash(b"short")


def test_version_key_ordering():
    object_id = "object-001"

    sequences = [10, 1, 2, 100, 3]

    keys = [KeyCodec.version_key(object_id, sequence) for sequence in sequences]

    sorted_sequences = [
        KeyCodec.decode_uint64(key, len(KeyCodec.encode_string(object_id)))[0]
        for key in sorted(keys)
    ]

    assert sorted_sequences == sorted(sequences)


def test_version_prefix_isolation():
    prefix_a = KeyCodec.version_prefix("object-A")
    prefix_b = KeyCodec.version_prefix("object-B")

    key_a = KeyCodec.version_key("object-A", 1)

    assert key_a.startswith(prefix_a)
    assert not key_a.startswith(prefix_b)


def test_chunk_dedup_key_is_deterministic():
    content_hash = bytes(range(32))

    key_1 = KeyCodec.chunk_dedup_key("object-001", content_hash, 1024)
    key_2 = KeyCodec.chunk_dedup_key("object-001", content_hash, 1024)

    assert key_1 == key_2


def test_order_key_encoding_preserves_lexicographic_order():
    order_keys = ["a0", "a1", "a2"]

    encoded = [KeyCodec.encode_order_key(key) for key in order_keys]

    assert encoded == sorted(encoded)


def test_object_chunk_index_key_uses_direct_order_key():
    prefix = KeyCodec.object_chunk_index_prefix("object-001")

    key = KeyCodec.object_chunk_index_key("object-001", "a1")

    assert key == prefix + b"a1"


def test_decode_string_rejects_invalid_utf8():
    data = b"\x00\x00\x00\x01\xff"

    with pytest.raises(KeyCodecError):
        KeyCodec.decode_string(data)


def test_decode_uint64_rejects_incomplete_data():
    with pytest.raises(KeyCodecError):
        KeyCodec.decode_uint64(b"\x00\x01")


def test_decode_bytes_rejects_incomplete_data():
    with pytest.raises(KeyCodecError):
        KeyCodec.decode_bytes(b"\x00\x00")
