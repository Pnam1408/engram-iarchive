"""Membership proof construction and verification for the Version History MMR.

The Core accepts mountain sibling hashes from its caller. It deliberately does
not retrieve or persist internal nodes; that responsibility belongs to the
Metadata integration layer.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..common.exceptions import InvalidInputError, InvalidTypeError
from ..common.validation import require_bytes, require_length, require_uint
from .hashing import DIGEST_BYTES, hash_leaf, hash_node
from .models import MMRState, Peak, validate_mmr_state, validate_peak
from .operations import calculate_root


@dataclass(frozen=True, slots=True)
class MMRNodePosition:
    """Location of a perfect subtree, used to retrieve a proof sibling."""

    start_leaf: int
    height: int

    def __post_init__(self) -> None:
        start_leaf = require_uint(self.start_leaf, 64, name="start_leaf")
        height = require_uint(self.height, 8, name="height")
        if height > 63:
            raise InvalidInputError("height exceeds the supported u64 MMR range")
        if start_leaf % (1 << height) != 0:
            raise InvalidInputError("sibling subtree start_leaf must be aligned")


@dataclass(frozen=True, slots=True)
class MMRMembershipProof:
    """Proof that a Version Manifest Hash occupies ``leaf_index`` in an MMR.

    ``siblings`` are ordered bottom-up along the target perfect-tree mountain.
    ``other_peaks`` are ordered left-to-right, omitting the target mountain.
    Sibling orientation is derived from the leaf's local index in that mountain.
    """

    leaf_index: int
    leaf_count: int
    version_manifest_hash: bytes
    siblings: tuple[bytes, ...]
    other_peaks: tuple[Peak, ...]

    def __post_init__(self) -> None:
        validate_membership_proof(self)


def _mountain_layout(leaf_count: int) -> tuple[tuple[int, int], ...]:
    """Return (start, height) for each peak, from left to right."""
    heights = tuple(
        height for height in range(63, -1, -1) if (leaf_count >> height) & 1
    )
    offset = 0
    layout: list[tuple[int, int]] = []
    for height in heights:
        layout.append((offset, height))
        offset += 1 << height
    return tuple(layout)


def _locate_peak(leaf_index: int, leaf_count: int) -> tuple[int, int, int]:
    """Return target (peak_index, peak_start, peak_height)."""
    for peak_index, (start, height) in enumerate(_mountain_layout(leaf_count)):
        if start <= leaf_index < start + (1 << height):
            return peak_index, start, height
    # The caller validates leaf_index first; this is a defensive guard.
    raise InvalidInputError("leaf_index does not belong to an MMR peak")


def membership_sibling_positions(
    leaf_index: int, leaf_count: int
) -> tuple[MMRNodePosition, ...]:
    """Return sibling subtree coordinates in bottom-up proof order.

    Height-zero positions identify sibling leaves. Higher positions identify
    internal nodes. The returned coordinates are suitable for direct lookup
    in an MMR node repository using ``(object_id, start_leaf, height)``.
    """
    leaf_count = require_uint(leaf_count, 64, name="leaf_count")
    leaf_index = require_uint(leaf_index, 64, name="leaf_index")
    if leaf_count == 0 or leaf_index >= leaf_count:
        raise InvalidInputError("leaf_index must be within a non-empty MMR")

    _, peak_start, peak_height = _locate_peak(leaf_index, leaf_count)
    local_index = leaf_index - peak_start
    positions: list[MMRNodePosition] = []
    for level in range(peak_height):
        subtree_size = 1 << level
        current_node_index = local_index >> level
        sibling_node_index = current_node_index ^ 1
        sibling_start = peak_start + sibling_node_index * subtree_size
        positions.append(MMRNodePosition(start_leaf=sibling_start, height=level))
    return tuple(positions)


def validate_membership_proof(proof: MMRMembershipProof) -> MMRMembershipProof:
    """Validate proof shape and all canonical ordering constraints."""
    if type(proof) is not MMRMembershipProof:
        raise InvalidTypeError("proof must be an MMRMembershipProof")

    leaf_count = require_uint(proof.leaf_count, 64, name="leaf_count")
    leaf_index = require_uint(proof.leaf_index, 64, name="leaf_index")
    if leaf_count == 0 or leaf_index >= leaf_count:
        raise InvalidInputError("leaf_index must be within a non-empty MMR")

    version_hash = require_bytes(
        proof.version_manifest_hash, name="version_manifest_hash"
    )
    require_length(
        version_hash, DIGEST_BYTES, DIGEST_BYTES, name="version_manifest_hash"
    )

    if type(proof.siblings) is not tuple:
        raise InvalidTypeError("siblings must be a tuple")
    if type(proof.other_peaks) is not tuple:
        raise InvalidTypeError("other_peaks must be a tuple")

    peak_index, _, peak_height = _locate_peak(leaf_index, leaf_count)
    if len(proof.siblings) != peak_height:
        raise InvalidInputError("sibling count must equal the target peak height")
    for sibling in proof.siblings:
        require_bytes(sibling, name="sibling hash")
        require_length(sibling, DIGEST_BYTES, DIGEST_BYTES, name="sibling hash")

    expected_heights = tuple(height for _, height in _mountain_layout(leaf_count))
    expected_other_heights = (
        expected_heights[:peak_index] + expected_heights[peak_index + 1 :]
    )
    if len(proof.other_peaks) != len(expected_other_heights):
        raise InvalidInputError("other_peaks count does not match MMR shape")
    for peak, expected_height in zip(proof.other_peaks, expected_other_heights):
        validate_peak(peak)
        if peak.height != expected_height:
            raise InvalidInputError(
                "other_peaks must preserve canonical left-to-right peak heights"
            )

    return proof


def _reconstruct_target_peak(proof: MMRMembershipProof) -> Peak:
    peak_index, peak_start, peak_height = _locate_peak(
        proof.leaf_index, proof.leaf_count
    )
    del peak_index  # The caller uses the index when rebuilding the full peak list.
    local_index = proof.leaf_index - peak_start
    current_hash = hash_leaf(proof.version_manifest_hash)

    for level, sibling_hash in enumerate(proof.siblings):
        if (local_index >> level) & 1:
            current_hash = hash_node(sibling_hash, current_hash)
        else:
            current_hash = hash_node(current_hash, sibling_hash)

    return Peak(height=peak_height, hash=current_hash)


def create_membership_proof(
    state: MMRState,
    *,
    leaf_index: int,
    version_manifest_hash: bytes,
    siblings: tuple[bytes, ...],
) -> MMRMembershipProof:
    """Create a proof using sibling hashes supplied by the caller.

    The supplied path is checked against the target peak already present in
    ``state``. The function does not access storage or infer sibling hashes.
    """
    validate_mmr_state(state)
    leaf_index = require_uint(leaf_index, 64, name="leaf_index")
    if leaf_index >= state.leaf_count:
        raise InvalidInputError("leaf_index must be within the MMR")

    if type(siblings) is not tuple:
        raise InvalidTypeError("siblings must be a tuple")
    peak_index, _, peak_height = _locate_peak(leaf_index, state.leaf_count)
    if len(siblings) != peak_height:
        raise InvalidInputError("sibling count must equal the target peak height")

    proof = MMRMembershipProof(
        leaf_index=leaf_index,
        leaf_count=state.leaf_count,
        version_manifest_hash=version_manifest_hash,
        siblings=siblings,
        other_peaks=tuple(
            peak for index, peak in enumerate(state.peaks) if index != peak_index
        ),
    )
    reconstructed = _reconstruct_target_peak(proof)
    if reconstructed.hash != state.peaks[peak_index].hash:
        raise InvalidInputError(
            "sibling path does not reconstruct the target peak in state"
        )
    return proof


def verify_membership_proof(proof: MMRMembershipProof, expected_root: bytes) -> bool:
    """Verify a membership proof against an expected MMR root.

    Malformed proof structure raises the relevant validation exception;
    a structurally valid proof that does not match ``expected_root`` returns
    ``False``.
    """
    validate_membership_proof(proof)
    require_bytes(expected_root, name="expected_root")
    require_length(expected_root, DIGEST_BYTES, DIGEST_BYTES, name="expected_root")

    peak_index, _, _ = _locate_peak(proof.leaf_index, proof.leaf_count)
    target_peak = _reconstruct_target_peak(proof)

    peaks: list[Peak] = []
    other_iter = iter(proof.other_peaks)
    for index in range(len(proof.other_peaks) + 1):
        peaks.append(target_peak if index == peak_index else next(other_iter))

    reconstructed_state = MMRState(
        leaf_count=proof.leaf_count,
        peaks=tuple(peaks),
    )
    return calculate_root(reconstructed_state) == expected_root
