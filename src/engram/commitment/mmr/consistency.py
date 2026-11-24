"""Stateful append-only consistency proofs for the Version History MMR.

The proof carries only canonical subtree peaks appended after an old state.
The verifier must already possess that old state and its trusted root.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..common.exceptions import InvalidInputError, InvalidTypeError
from ..common.validation import require_bytes, require_length, require_uint
from .hashing import DIGEST_BYTES
from .models import MMRState, Peak, validate_mmr_state, validate_peak
from .operations import append_subtree, calculate_root


def canonical_subtree_heights(old_leaf_count: int, new_leaf_count: int) -> tuple[int, ...]:
    """Return the largest-aligned-power-of-two decomposition of [old, new).

    At each cursor, select the largest subtree that is both aligned at the
    cursor and no larger than the remaining range.
    """
    old_count = require_uint(old_leaf_count, 64, name="old_leaf_count")
    new_count = require_uint(new_leaf_count, 64, name="new_leaf_count")
    if new_count < old_count:
        raise InvalidInputError("new_leaf_count must not be smaller than old_leaf_count")

    heights: list[int] = []
    cursor = old_count
    while cursor < new_count:
        remaining = new_count - cursor
        # The largest subtree allowed by the remaining range.
        height_by_remaining = remaining.bit_length() - 1
        # At cursor zero every height is aligned; otherwise alignment is
        # bounded by the least-significant set bit of cursor.
        height_by_alignment = 63 if cursor == 0 else (cursor & -cursor).bit_length() - 1
        height = min(height_by_remaining, height_by_alignment, 63)
        heights.append(height)
        cursor += 1 << height
    return tuple(heights)


@dataclass(frozen=True, slots=True)
class MMRConsistencyProof:
    """Append-only proof relative to a verifier-held old MMR state."""

    old_leaf_count: int
    new_leaf_count: int
    appended_subtrees: tuple[Peak, ...]

    def __post_init__(self) -> None:
        validate_consistency_proof(self)


def validate_consistency_proof(proof: MMRConsistencyProof) -> MMRConsistencyProof:
    """Validate counts, subtree digests, and canonical range decomposition."""
    if type(proof) is not MMRConsistencyProof:
        raise InvalidTypeError("proof must be an MMRConsistencyProof")
    old_count = require_uint(proof.old_leaf_count, 64, name="old_leaf_count")
    new_count = require_uint(proof.new_leaf_count, 64, name="new_leaf_count")
    if new_count < old_count:
        raise InvalidInputError("new_leaf_count must not be smaller than old_leaf_count")
    if type(proof.appended_subtrees) is not tuple:
        raise InvalidTypeError("appended_subtrees must be a tuple")

    expected_heights = canonical_subtree_heights(old_count, new_count)
    if len(proof.appended_subtrees) != len(expected_heights):
        raise InvalidInputError("appended_subtrees do not match canonical decomposition")
    for subtree, expected_height in zip(proof.appended_subtrees, expected_heights):
        validate_peak(subtree)
        if subtree.height != expected_height:
            raise InvalidInputError("appended subtree heights are not canonical")
    return proof


def create_consistency_proof(
    old_leaf_count: int,
    new_leaf_count: int,
    appended_subtrees: tuple[Peak, ...],
) -> MMRConsistencyProof:
    """Construct a proof from caller-supplied hashes of canonical subtrees.

    Core MMR does not retrieve subtree hashes from storage. The caller (later,
    typically the Metadata/Application integration) supplies them in order.
    """
    return MMRConsistencyProof(
        old_leaf_count=old_leaf_count,
        new_leaf_count=new_leaf_count,
        appended_subtrees=appended_subtrees,
    )


def verify_consistency_proof(
    old_state: MMRState,
    proof: MMRConsistencyProof,
    *,
    expected_old_root: bytes,
    expected_new_root: bytes,
) -> bool:
    """Verify extension from a trusted old state to an expected new root.

    Malformed proof inputs raise validation exceptions. A structurally valid
    proof that fails either root comparison returns ``False``. The supplied
    old state is never mutated.
    """
    validate_mmr_state(old_state)
    validate_consistency_proof(proof)
    if old_state.leaf_count != proof.old_leaf_count:
        raise InvalidInputError("old_state leaf_count does not match proof")

    require_bytes(expected_old_root, name="expected_old_root")
    require_length(expected_old_root, DIGEST_BYTES, DIGEST_BYTES, name="expected_old_root")
    require_bytes(expected_new_root, name="expected_new_root")
    require_length(expected_new_root, DIGEST_BYTES, DIGEST_BYTES, name="expected_new_root")

    if calculate_root(old_state) != expected_old_root:
        return False

    updated_state = old_state
    for subtree in proof.appended_subtrees:
        updated_state = append_subtree(updated_state, subtree)

    if updated_state.leaf_count != proof.new_leaf_count:
        # This is also guaranteed by canonical decomposition, retained as a
        # defensive invariant at the verification boundary.
        raise InvalidInputError("proof subtrees do not reach new_leaf_count")
    return calculate_root(updated_state) == expected_new_root
