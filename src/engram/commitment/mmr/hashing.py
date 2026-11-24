"""SHA-256 hashing primitives for the Version History MMR.

Protocol framing (all integer fields are unsigned big-endian):

* Leaf: ``H(EncodeString(LEAF_DOMAIN) || version_manifest_hash)``
* Node: ``H(EncodeString(NODE_DOMAIN) || left_hash || right_hash)``
* Bag peak: ``H(EncodeString(BAG_DOMAIN) || 0x01 || u8(height) || peak_hash)``
* Bag node: ``H(EncodeString(BAG_DOMAIN) || 0x02 || u8(next_height) ||
  accumulator || next_peak_hash)``
* Non-empty root: ``H(EncodeString(ROOT_DOMAIN) || u64(leaf_count) ||
  u32(peak_count) || bagged_hash)``
* Empty root: ``H(EncodeString(ROOT_DOMAIN) || u64(0) || u32(0))``

``H`` denotes SHA-256. Hash fields are raw, fixed-width 32-byte digests.
"""

from __future__ import annotations

from ..common.domains import (
    VERSION_MMR_BAG,
    VERSION_MMR_LEAF,
    VERSION_MMR_NODE,
    VERSION_MMR_ROOT,
)
from ..common.encoding import encode_u8, encode_u32, encode_u64
from ..common.exceptions import InvalidInputError
from ..common.hashing import hash_with_domain
from ..common.validation import require_bytes, require_length, require_uint

DIGEST_BYTES = 32
MAX_MMR_LEAF_COUNT = (1 << 64) - 1
MAX_PEAK_HEIGHT = 63

# Operation tags within the VERSION_MMR_BAG domain.
BAG_PEAK_TAG = 0x01
BAG_NODE_TAG = 0x02


def _require_digest(value: bytes, *, name: str) -> bytes:
    require_bytes(value, name=name)
    require_length(value, DIGEST_BYTES, DIGEST_BYTES, name=name)
    return value


def hash_leaf(version_manifest_hash: bytes) -> bytes:
    """Hash one 32-byte Version Manifest Hash into an MMR leaf."""
    digest = _require_digest(version_manifest_hash, name="version_manifest_hash")
    return hash_with_domain(VERSION_MMR_LEAF, digest)


def hash_node(left_hash: bytes, right_hash: bytes) -> bytes:
    """Hash two ordered child digests into an internal MMR node."""
    left = _require_digest(left_hash, name="left_hash")
    right = _require_digest(right_hash, name="right_hash")
    return hash_with_domain(VERSION_MMR_NODE, left + right)


def hash_bag_peak(height: int, peak_hash: bytes) -> bytes:
    """Encode the first peak for left-to-right peak bagging."""
    height = require_uint(height, 8, name="height")
    if height > MAX_PEAK_HEIGHT:
        raise InvalidInputError("height exceeds the supported u64 MMR range")
    digest = _require_digest(peak_hash, name="peak_hash")
    payload = encode_u8(BAG_PEAK_TAG) + encode_u8(height) + digest
    return hash_with_domain(VERSION_MMR_BAG, payload)


def hash_bag_node(
    accumulator_hash: bytes, next_peak_height: int, next_peak_hash: bytes
) -> bytes:
    """Fold the next peak into the left-to-right bagging accumulator.

    The next peak's height is bound explicitly, as peak heights are part of
    the canonical MMR shape and are needed to distinguish bagging inputs.
    """
    accumulator = _require_digest(accumulator_hash, name="accumulator_hash")
    height = require_uint(next_peak_height, 8, name="next_peak_height")
    if height > MAX_PEAK_HEIGHT:
        raise InvalidInputError("height exceeds the supported u64 MMR range")
    peak = _require_digest(next_peak_hash, name="next_peak_hash")
    payload = encode_u8(BAG_NODE_TAG) + encode_u8(height) + accumulator + peak
    return hash_with_domain(VERSION_MMR_BAG, payload)


def hash_root(leaf_count: int, peak_count: int, bagged_hash: bytes | None = None) -> bytes:
    """Bind the MMR size and peak count to its bagged peak commitment.

    Empty MMRs have no bagged hash. Non-empty MMRs must supply one. The
    peak count is checked against popcount(leaf_count), preventing callers
    from constructing a root with an impossible peak cardinality.
    """
    count = require_uint(leaf_count, 64, name="leaf_count")
    peaks = require_uint(peak_count, 32, name="peak_count")

    if peaks != count.bit_count():
        raise InvalidInputError("peak_count must equal popcount(leaf_count)")

    payload = encode_u64(count) + encode_u32(peaks)
    if count == 0:
        if bagged_hash is not None:
            raise InvalidInputError("empty MMR root must not include a bagged_hash")
    else:
        if bagged_hash is None:
            raise InvalidInputError("non-empty MMR root requires a bagged_hash")
        payload += _require_digest(bagged_hash, name="bagged_hash")

    return hash_with_domain(VERSION_MMR_ROOT, payload)
