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
