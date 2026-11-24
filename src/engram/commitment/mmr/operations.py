"""Append and root operations for the Version History MMR."""

from __future__ import annotations

from .hashing import (
    DIGEST_BYTES,
    MAX_MMR_LEAF_COUNT,
    hash_bag_node,
    hash_bag_peak,
    hash_leaf,
    hash_node,
    hash_root,
)
from .models import MMRState, Peak, validate_mmr_state, validate_peak
from ..common.exceptions import InvalidInputError
from ..common.validation import require_bytes, require_length


def append_subtree(state: MMRState, subtree: Peak) -> MMRState:
    """Append an already-hashed perfect subtree to ``state``.

    The subtree must begin at a leaf offset aligned to its size (2**height).
    The input state and subtree are never mutated.
    """
    validate_mmr_state(state)
    validate_peak(subtree)

    subtree_size = 1 << subtree.height
    if state.leaf_count % subtree_size != 0:
        raise InvalidInputError(
            "subtree is not aligned with the current leaf_count"
        )
    if state.leaf_count > MAX_MMR_LEAF_COUNT - subtree_size:
        raise InvalidInputError("append would exceed the supported u64 leaf_count")

    peaks = list(state.peaks)
    current = subtree
    while peaks and peaks[-1].height == current.height:
        left = peaks.pop()
        current = Peak(
            height=current.height + 1,
            hash=hash_node(left.hash, current.hash),
        )

    peaks.append(current)
    new_state = MMRState(
        leaf_count=state.leaf_count + subtree_size,
        peaks=tuple(peaks),
    )
    return validate_mmr_state(new_state)


def append_leaf(state: MMRState, version_manifest_hash: bytes) -> MMRState:
    """Append one Version Manifest Hash as a new MMR leaf."""
    validate_mmr_state(state)
    require_bytes(version_manifest_hash, name="version_manifest_hash")
    require_length(
        version_manifest_hash,
        DIGEST_BYTES,
        DIGEST_BYTES,
        name="version_manifest_hash",
    )
    leaf_hash = hash_leaf(version_manifest_hash)
    return append_subtree(state, Peak(height=0, hash=leaf_hash))


def calculate_root(state: MMRState) -> bytes:
    """Calculate the canonical MMR root using left-to-right peak bagging."""
    validate_mmr_state(state)

    if state.leaf_count == 0:
        return hash_root(leaf_count=0, peak_count=0)

    first, *remaining = state.peaks
    accumulator = hash_bag_peak(first.height, first.hash)
    for peak in remaining:
        accumulator = hash_bag_node(
            accumulator,
            next_peak_height=peak.height,
            next_peak_hash=peak.hash,
        )

    return hash_root(
        leaf_count=state.leaf_count,
        peak_count=len(state.peaks),
        bagged_hash=accumulator,
    )
