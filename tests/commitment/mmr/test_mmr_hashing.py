import hashlib

import pytest

from engram.commitment.common.domains import (
    VERSION_MMR_BAG, VERSION_MMR_LEAF, VERSION_MMR_NODE, VERSION_MMR_ROOT,
)
from engram.commitment.common.encoding import encode_string, encode_u8, encode_u32, encode_u64
from engram.commitment.common.exceptions import InvalidInputError, InvalidTypeError, ValueOutOfRangeError
from engram.commitment.mmr.hashing import (
    BAG_NODE_TAG, BAG_PEAK_TAG, hash_bag_node, hash_bag_peak, hash_leaf,
    hash_node, hash_root,
)


def _expected(domain: str, payload: bytes) -> bytes:
    return hashlib.sha256(encode_string(domain) + payload).digest()


def test_leaf_hash_uses_version_manifest_hash_as_fixed_32_byte_input():
    manifest_hash = bytes(range(32))
    assert hash_leaf(manifest_hash) == _expected(VERSION_MMR_LEAF, manifest_hash)


def test_internal_node_hash_is_ordered():
    left = b"\x01" * 32
    right = b"\x02" * 32
    assert hash_node(left, right) == _expected(VERSION_MMR_NODE, left + right)
    assert hash_node(left, right) != hash_node(right, left)


def test_bag_peak_encoding_is_exact():
    peak_hash = b"\x11" * 32
    payload = encode_u8(BAG_PEAK_TAG) + encode_u8(3) + peak_hash
    assert hash_bag_peak(3, peak_hash) == _expected(VERSION_MMR_BAG, payload)


def test_bag_node_encoding_is_exact_and_ordered():
    accumulator = b"\xaa" * 32
    next_peak = b"\xbb" * 32
    payload = encode_u8(BAG_NODE_TAG) + encode_u8(1) + accumulator + next_peak
    assert hash_bag_node(accumulator, 1, next_peak) == _expected(VERSION_MMR_BAG, payload)
    assert hash_bag_node(accumulator, 1, next_peak) != hash_bag_node(next_peak, 1, accumulator)


def test_root_encoding_for_non_empty_mmr_is_exact():
    bagged = b"\xcc" * 32
    payload = encode_u64(11) + encode_u32(3) + bagged
    assert hash_root(11, 3, bagged) == _expected(VERSION_MMR_ROOT, payload)


def test_empty_root_has_no_bagged_hash():
    payload = encode_u64(0) + encode_u32(0)
    assert hash_root(0, 0) == _expected(VERSION_MMR_ROOT, payload)


@pytest.mark.parametrize("bad", [bytearray(32), memoryview(bytes(32)), "00" * 32, b"x" * 31, b"x" * 33])
def test_digest_inputs_are_strict(bad):
    with pytest.raises((InvalidTypeError, ValueOutOfRangeError)):
        hash_leaf(bad)


@pytest.mark.parametrize("bad", [True, False, 1.0, "1", -1, 256])
def test_bag_height_rejects_invalid_values(bad):
    with pytest.raises((InvalidTypeError, ValueOutOfRangeError)):
        hash_bag_peak(bad, b"x" * 32)


def test_bag_height_rejects_values_outside_u64_mmr_range():
    with pytest.raises(InvalidInputError):
        hash_bag_peak(64, b"x" * 32)


def test_root_requires_canonical_peak_count():
    with pytest.raises(InvalidInputError):
        hash_root(11, 2, b"x" * 32)


def test_root_requires_bagged_hash_only_for_non_empty_mmr():
    with pytest.raises(InvalidInputError):
        hash_root(1, 1)
    with pytest.raises(InvalidInputError):
        hash_root(0, 0, b"x" * 32)


def test_root_rejects_invalid_integer_types():
    with pytest.raises(InvalidTypeError):
        hash_root(True, 1, b"x" * 32)
    with pytest.raises(InvalidTypeError):
        hash_root(1, True, b"x" * 32)
