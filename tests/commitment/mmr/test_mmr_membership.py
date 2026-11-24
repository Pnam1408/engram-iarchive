import pytest

from engram.commitment.common.exceptions import InvalidInputError, InvalidTypeError
from engram.commitment.mmr import (
    MMRMembershipProof,
    MMRState,
    Peak,
    append_leaf,
    calculate_root,
    create_membership_proof,
    hash_leaf,
    hash_node,
    verify_membership_proof,
)


def _digest(value: int) -> bytes:
    return value.to_bytes(32, "big")


def _build_state_and_hashes(count: int):
    version_hashes = [_digest(i + 100) for i in range(count)]
    state = MMRState(0, ())
    for version_hash in version_hashes:
        state = append_leaf(state, version_hash)
    return state, version_hashes


def _layout(count: int):
    offset = 0
    result = []
    for height in range(63, -1, -1):
        if (count >> height) & 1:
            result.append((offset, height))
            offset += 1 << height
    return result


def _siblings_for(version_hashes, leaf_index, leaf_count):
    for start, height in _layout(leaf_count):
        size = 1 << height
        if start <= leaf_index < start + size:
            local_index = leaf_index - start
            level = [hash_leaf(value) for value in version_hashes[start:start + size]]
            siblings = []
            while len(level) > 1:
                siblings.append(level[local_index ^ 1])
                level = [hash_node(level[i], level[i + 1]) for i in range(0, len(level), 2)]
                local_index //= 2
            return tuple(siblings)
    raise AssertionError("leaf not found")


@pytest.mark.parametrize("count", range(1, 18))
def test_membership_proof_round_trip_for_every_leaf(count):
    state, version_hashes = _build_state_and_hashes(count)
    root = calculate_root(state)
    for leaf_index, version_hash in enumerate(version_hashes):
        proof = create_membership_proof(
            state,
            leaf_index=leaf_index,
            version_manifest_hash=version_hash,
            siblings=_siblings_for(version_hashes, leaf_index, count),
        )
        assert verify_membership_proof(proof, root)
        assert proof.leaf_index == leaf_index
        assert proof.leaf_count == count


def test_proof_reconstructs_correct_peak_and_other_peaks_order():
    state, version_hashes = _build_state_and_hashes(11)  # heights [3, 1, 0]
    proof = create_membership_proof(
        state,
        leaf_index=9,
        version_manifest_hash=version_hashes[9],
        siblings=_siblings_for(version_hashes, 9, 11),
    )
    assert tuple(peak.height for peak in proof.other_peaks) == (3, 0)
    assert verify_membership_proof(proof, calculate_root(state))


def test_wrong_root_returns_false():
    state, version_hashes = _build_state_and_hashes(5)
    proof = create_membership_proof(
        state, leaf_index=4, version_manifest_hash=version_hashes[4], siblings=()
    )
    assert not verify_membership_proof(proof, _digest(999))


def test_wrong_version_hash_returns_false():
    state, version_hashes = _build_state_and_hashes(5)
    proof = create_membership_proof(
        state, leaf_index=4, version_manifest_hash=version_hashes[4], siblings=()
    )
    forged = MMRMembershipProof(
        leaf_index=proof.leaf_index,
        leaf_count=proof.leaf_count,
        version_manifest_hash=_digest(999),
        siblings=proof.siblings,
        other_peaks=proof.other_peaks,
    )
    assert not verify_membership_proof(forged, calculate_root(state))


def test_wrong_sibling_returns_false():
    state, version_hashes = _build_state_and_hashes(8)
    siblings = list(_siblings_for(version_hashes, 3, 8))
    siblings[0] = _digest(999)
    proof = MMRMembershipProof(
        leaf_index=3,
        leaf_count=8,
        version_manifest_hash=version_hashes[3],
        siblings=tuple(siblings),
        other_peaks=(),
    )
    assert not verify_membership_proof(proof, calculate_root(state))


def test_create_rejects_sibling_path_not_matching_state_peak():
    state, version_hashes = _build_state_and_hashes(4)
    siblings = list(_siblings_for(version_hashes, 0, 4))
    siblings[0] = _digest(999)
    with pytest.raises(InvalidInputError, match="does not reconstruct"):
        create_membership_proof(
            state,
            leaf_index=0,
            version_manifest_hash=version_hashes[0],
            siblings=tuple(siblings),
        )


@pytest.mark.parametrize("leaf_index", [-1, 5, True])
def test_proof_rejects_invalid_leaf_index(leaf_index):
    with pytest.raises((InvalidInputError, InvalidTypeError, ValueError, TypeError)):
        MMRMembershipProof(leaf_index, 5, _digest(1), (), (Peak(2, _digest(2)), Peak(0, _digest(3))))


def test_proof_rejects_wrong_sibling_count():
    with pytest.raises(InvalidInputError, match="sibling count"):
        MMRMembershipProof(leaf_index=0, leaf_count=4, version_manifest_hash=_digest(1),
                           siblings=(), other_peaks=())


def test_proof_rejects_noncanonical_other_peak_order():
    state, version_hashes = _build_state_and_hashes(11)
    proof = create_membership_proof(
        state, leaf_index=9, version_manifest_hash=version_hashes[9],
        siblings=_siblings_for(version_hashes, 9, 11),
    )
    with pytest.raises(InvalidInputError, match="canonical"):
        MMRMembershipProof(
            leaf_index=proof.leaf_index,
            leaf_count=proof.leaf_count,
            version_manifest_hash=proof.version_manifest_hash,
            siblings=proof.siblings,
            other_peaks=tuple(reversed(proof.other_peaks)),
        )


def test_proof_requires_tuple_collections():
    with pytest.raises(InvalidTypeError):
        MMRMembershipProof(0, 1, _digest(1), [], ())
    with pytest.raises(InvalidTypeError):
        MMRMembershipProof(0, 1, _digest(1), (), [])


def test_verifier_rejects_wrong_expected_root_type_or_length():
    state, version_hashes = _build_state_and_hashes(1)
    proof = create_membership_proof(
        state, leaf_index=0, version_manifest_hash=version_hashes[0], siblings=()
    )
    with pytest.raises(InvalidTypeError):
        verify_membership_proof(proof, bytearray(32))
    with pytest.raises((InvalidInputError, ValueError)):
        verify_membership_proof(proof, b"short")
