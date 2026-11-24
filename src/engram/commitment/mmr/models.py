"""Immutable data models for the Version History MMR."""

from __future__ import annotations

from dataclasses import dataclass

from ..common.exceptions import InvalidInputError, InvalidTypeError
from ..common.validation import require_bytes, require_length, require_uint
from .hashing import DIGEST_BYTES, MAX_MMR_LEAF_COUNT, MAX_PEAK_HEIGHT


@dataclass(frozen=True, slots=True)
class Peak:
    """One perfect binary tree peak in an MMR.

    ``height`` is the tree height (a leaf has height zero), and ``hash`` is
    the 32-byte root digest of that perfect tree.
    """

    height: int
    hash: bytes

    def __post_init__(self) -> None:
        height = require_uint(self.height, 8, name="height")
        if height > MAX_PEAK_HEIGHT:
            raise InvalidInputError(
                "height exceeds the supported u64 MMR range"
            )
        digest = require_bytes(self.hash, name="hash")
        require_length(digest, DIGEST_BYTES, DIGEST_BYTES, name="hash")


@dataclass(frozen=True, slots=True)
class MMRState:
    """Canonical append state represented by left-to-right MMR peaks.

    Peaks must be ordered by strictly descending height. Their heights are
    exactly the set-bit positions of ``leaf_count`` in descending order.
    """

    leaf_count: int
    peaks: tuple[Peak, ...]

    def __post_init__(self) -> None:
        validate_mmr_state(self)


def validate_peak(peak: Peak) -> Peak:
    """Validate a Peak instance and return it unchanged."""
    if type(peak) is not Peak:
        raise InvalidTypeError("peak must be a Peak")
    # Re-check fields so validation remains explicit at trust boundaries.
    require_uint(peak.height, 8, name="height")
    if peak.height > MAX_PEAK_HEIGHT:
        raise InvalidInputError("height exceeds the supported u64 MMR range")
    digest = require_bytes(peak.hash, name="hash")
    require_length(digest, DIGEST_BYTES, DIGEST_BYTES, name="hash")
    return peak


def validate_mmr_state(state: MMRState) -> MMRState:
    """Validate all structural invariants of an MMR state.

    This function performs no repair, sorting, coercion, or normalization.
    """
    if type(state) is not MMRState:
        raise InvalidTypeError("state must be an MMRState")

    leaf_count = require_uint(
        state.leaf_count, 64, name="leaf_count"
    )
    if leaf_count > MAX_MMR_LEAF_COUNT:  # Defensive; require_uint already bounds it.
        raise InvalidInputError("leaf_count exceeds the supported MMR range")

    if type(state.peaks) is not tuple:
        raise InvalidTypeError("peaks must be a tuple")

    expected_heights = tuple(
        height
        for height in range(MAX_PEAK_HEIGHT, -1, -1)
        if (leaf_count >> height) & 1
    )

    if len(state.peaks) != len(expected_heights):
        raise InvalidInputError(
            "peak count must equal popcount(leaf_count)"
        )

    actual_heights: list[int] = []
    total_leaves = 0
    for index, peak in enumerate(state.peaks):
        validate_peak(peak)
        actual_heights.append(peak.height)
        total_leaves += 1 << peak.height
        if index > 0 and state.peaks[index - 1].height <= peak.height:
            raise InvalidInputError(
                "peak heights must be strictly descending from left to right"
            )

    if tuple(actual_heights) != expected_heights:
        raise InvalidInputError(
            "peak heights must match the set-bit positions of leaf_count"
        )

    if total_leaves != leaf_count:
        raise InvalidInputError(
            "sum of peak sizes must equal leaf_count"
        )

    return state


@dataclass(frozen=True, slots=True)
class MMRCreatedNode:
    """An internal node created while appending to an MMR.

    ``start_leaf`` is the first leaf covered by the perfect subtree.
    Created nodes always have height >= 1 because leaf hashes are derived
    from Version Manifest Hashes and are not persisted as internal nodes.
    """

    start_leaf: int
    height: int
    hash: bytes

    def __post_init__(self) -> None:
        validate_created_node(self)


@dataclass(frozen=True, slots=True)
class MMRAppendResult:
    """Result of appending one Version leaf, including new internal nodes."""

    state: MMRState
    created_nodes: tuple[MMRCreatedNode, ...]

    def __post_init__(self) -> None:
        validate_mmr_append_result(self)


def validate_created_node(node: MMRCreatedNode) -> MMRCreatedNode:
    """Validate a newly-created internal node descriptor."""
    if type(node) is not MMRCreatedNode:
        raise InvalidTypeError("node must be an MMRCreatedNode")
    start_leaf = require_uint(node.start_leaf, 64, name="start_leaf")
    height = require_uint(node.height, 8, name="height")
    if not 1 <= height <= MAX_PEAK_HEIGHT:
        raise InvalidInputError("created internal node height must be in [1, 63]")
    if start_leaf % (1 << height) != 0:
        raise InvalidInputError("created internal node start_leaf must be aligned")
    digest = require_bytes(node.hash, name="hash")
    require_length(digest, DIGEST_BYTES, DIGEST_BYTES, name="hash")
    return node


def validate_mmr_append_result(result: MMRAppendResult) -> MMRAppendResult:
    """Validate the shape of an append result."""
    if type(result) is not MMRAppendResult:
        raise InvalidTypeError("result must be an MMRAppendResult")
    validate_mmr_state(result.state)
    if type(result.created_nodes) is not tuple:
        raise InvalidTypeError("created_nodes must be a tuple")
    previous_height = 0
    for index, node in enumerate(result.created_nodes):
        validate_created_node(node)
        if index and node.height <= previous_height:
            raise InvalidInputError(
                "created_nodes must be ordered by strictly increasing height"
            )
        previous_height = node.height
    return result
