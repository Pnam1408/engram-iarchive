from dataclasses import FrozenInstanceError

import pytest

from engram.commitment.common.exceptions import InvalidInputError, InvalidTypeError, ValueOutOfRangeError
from engram.commitment.mmr import (
    MMRConsistencyProof,
    MMRState,
    Peak,
    append_leaf,
    calculate_root,
    canonical_subtree_heights,
    create_consistency_proof,
    verify_consistency_proof,
)


def _state_with_leaves(count: int) -> tuple[MMRState, list[bytes]]:
    state = MMRState(leaf_count=0, peaks=())
    hashes = []
    for index in range(count):
        value = index.to_bytes(32, "big")
        hashes.append(value)
        state = append_leaf(state, value)
    return state, hashes


def _subtree_peak(version_hashes: list[bytes]) -> Peak:
    assert version_hashes
    state = MMRState(leaf_count=0, peaks=())
    for value in version_hashes:
        state = append_leaf(state, value)
    assert len(state.peaks) == 1
    return state.peaks[0]


@pytest.mark.parametrize(
    ("old_count", "new_count", "expected"),
    [
        (0, 0, ()),
        (0, 1, (0,)),
        (0, 7, (2, 1, 0)),
        (0, 11, (3, 1, 0)),
        (5, 9, (0, 1, 0)),
        (7, 8, (0,)),
        (8, 16, (3,)),
        (11, 11, ()),
        (11, 19, (0, 2, 1, 0)),
    ],
)
def test_canonical_subtree_decomposition(old_count, new_count, expected):
    assert canonical_subtree_heights(old_count, new_count) == expected


def test_consistency_proof_success_for_many_ranges():
    for old_count in range(0, 18):
        for new_count in range(old_count, 25):
            old_state, _ = _state_with_leaves(old_count)
            new_state, all_hashes = _state_with_leaves(new_count)
            heights = canonical_subtree_heights(old_count, new_count)
            subtrees = []
            cursor = old_count
            for height in heights:
                size = 1 << height
                subtrees.append(_subtree_peak(all_hashes[cursor:cursor + size]))
                cursor += size
            proof = create_consistency_proof(old_count, new_count, tuple(subtrees))
            assert verify_consistency_proof(
                old_state,
                proof,
                expected_old_root=calculate_root(old_state),
                expected_new_root=calculate_root(new_state),
            )


def test_empty_append_proof_keeps_root_unchanged():
    state, _ = _state_with_leaves(11)
    proof = create_consistency_proof(11, 11, ())
    root = calculate_root(state)
    assert verify_consistency_proof(
        state, proof, expected_old_root=root, expected_new_root=root
    )


def test_verification_rejects_wrong_old_root_and_new_root():
    old_state, _ = _state_with_leaves(5)
    new_state, hashes = _state_with_leaves(9)
    subtrees = (
        _subtree_peak(hashes[5:6]),
        _subtree_peak(hashes[6:8]),
        _subtree_peak(hashes[8:9]),
    )
    proof = create_consistency_proof(5, 9, subtrees)
    old_root, new_root = calculate_root(old_state), calculate_root(new_state)
    assert not verify_consistency_proof(
        old_state, proof, expected_old_root=b"x" * 32, expected_new_root=new_root
    )
    assert not verify_consistency_proof(
        old_state, proof, expected_old_root=old_root, expected_new_root=b"y" * 32
    )


def test_verification_does_not_mutate_old_state():
    old_state, _ = _state_with_leaves(7)
    new_state, hashes = _state_with_leaves(8)
    proof = create_consistency_proof(7, 8, (_subtree_peak(hashes[7:8]),))
    before = old_state
    assert verify_consistency_proof(
        old_state, proof,
        expected_old_root=calculate_root(old_state),
        expected_new_root=calculate_root(new_state),
    )
    assert old_state is before
    assert old_state.leaf_count == 7


def test_rejects_noncanonical_heights_and_subtree_count():
    with pytest.raises(InvalidInputError):
        MMRConsistencyProof(5, 9, (Peak(1, b"a" * 32), Peak(0, b"b" * 32), Peak(0, b"c" * 32)))
    with pytest.raises(InvalidInputError):
        MMRConsistencyProof(5, 9, (Peak(0, b"a" * 32),))


def test_rejects_decreasing_count_and_wrong_container_types():
    with pytest.raises(InvalidInputError):
        MMRConsistencyProof(4, 3, ())
    with pytest.raises(InvalidTypeError):
        MMRConsistencyProof(0, 1, [Peak(0, b"a" * 32)])
    with pytest.raises(InvalidInputError):
        canonical_subtree_heights(2, 1)


def test_rejects_old_state_count_mismatch():
    state, _ = _state_with_leaves(4)
    proof = create_consistency_proof(3, 4, (Peak(0, b"a" * 32),))
    with pytest.raises(InvalidInputError):
        verify_consistency_proof(
            state, proof,
            expected_old_root=calculate_root(state),
            expected_new_root=b"b" * 32,
        )


def test_rejects_bad_expected_root_types_and_lengths():
    state, _ = _state_with_leaves(0)
    proof = create_consistency_proof(0, 0, ())
    with pytest.raises(ValueOutOfRangeError):
        verify_consistency_proof(
            state, proof, expected_old_root=b"a" * 31, expected_new_root=b"b" * 32
        )


def test_proof_is_immutable():
    proof = create_consistency_proof(0, 0, ())
    with pytest.raises(FrozenInstanceError):
        proof.new_leaf_count = 1
