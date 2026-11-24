import pytest

from engram.commitment.common.exceptions import InvalidInputError, InvalidTypeError
from engram.commitment.mmr import (
    MAX_MMR_LEAF_COUNT,
    MMRState,
    Peak,
    append_leaf,
    append_subtree,
    calculate_root,
)


def _digest(value: int) -> bytes:
    return value.to_bytes(32, "big")


def _state_after(count: int) -> MMRState:
    state = MMRState(leaf_count=0, peaks=())
    for index in range(count):
        state = append_leaf(state, _digest(index + 1))
    return state


@pytest.mark.parametrize(
    ("count", "heights"),
    [
        (0, ()),
        (1, (0,)),
        (2, (1,)),
        (3, (1, 0)),
        (7, (2, 1, 0)),
        (8, (3,)),
        (11, (3, 1, 0)),
        (13, (3, 2, 0)),
    ],
)
def test_append_leaf_produces_canonical_peak_shape(count, heights):
    state = _state_after(count)
    assert state.leaf_count == count
    assert tuple(peak.height for peak in state.peaks) == heights


def test_append_leaf_merges_multiple_peaks_at_power_of_two_boundary():
    state = _state_after(7)
    updated = append_leaf(state, _digest(8))
    assert tuple(peak.height for peak in updated.peaks) == (3,)
    assert updated.leaf_count == 8


def test_append_leaf_does_not_mutate_input_state():
    state = _state_after(7)
    old_peaks = state.peaks
    updated = append_leaf(state, _digest(8))
    assert state.leaf_count == 7
    assert state.peaks is old_peaks
    assert updated is not state


@pytest.mark.parametrize("bad_hash", [bytearray(32), b"x" * 31, b"x" * 33, "x" * 32])
def test_append_leaf_rejects_invalid_version_manifest_hash(bad_hash):
    with pytest.raises((InvalidTypeError, InvalidInputError, ValueError)):
        append_leaf(MMRState(0, ()), bad_hash)


def test_append_subtree_accepts_aligned_subtree_and_merges():
    state = _state_after(6)  # peaks [2, 1]
    subtree = Peak(height=1, hash=_digest(99))
    updated = append_subtree(state, subtree)
    assert updated.leaf_count == 8
    assert tuple(peak.height for peak in updated.peaks) == (3,)


def test_append_subtree_accepts_large_aligned_subtree_without_leaf_expansion():
    state = MMRState(leaf_count=0, peaks=())
    updated = append_subtree(state, Peak(height=10, hash=_digest(77)))
    assert updated.leaf_count == 1 << 10
    assert tuple(peak.height for peak in updated.peaks) == (10,)


def test_append_subtree_rejects_unaligned_subtree():
    state = _state_after(5)
    with pytest.raises(InvalidInputError, match="aligned"):
        append_subtree(state, Peak(height=1, hash=_digest(90)))


def test_append_subtree_rejects_count_overflow():
    state = MMRState(
        leaf_count=MAX_MMR_LEAF_COUNT,
        peaks=tuple(
            Peak(height=h, hash=_digest(h + 1))
            for h in range(63, -1, -1)
        ),
    )
    with pytest.raises(InvalidInputError, match="exceed"):
        append_subtree(state, Peak(height=0, hash=_digest(100)))


def test_append_subtree_rejects_invalid_state_or_subtree():
    with pytest.raises(InvalidTypeError):
        append_subtree(object(), Peak(height=0, hash=_digest(1)))
    with pytest.raises(InvalidTypeError):
        append_subtree(MMRState(0, ()), object())


def test_calculate_empty_root_matches_empty_root_primitive():
    from engram.commitment.mmr import hash_root

    assert calculate_root(MMRState(0, ())) == hash_root(0, 0)


def test_calculate_root_is_deterministic_and_order_sensitive():
    state_a = _state_after(4)
    state_b = MMRState(leaf_count=4, peaks=(Peak(height=2, hash=_digest(123)),))
    assert calculate_root(state_a) == calculate_root(state_a)
    assert calculate_root(state_a) != calculate_root(state_b)


def test_calculate_root_changes_when_peak_hash_changes():
    state = _state_after(3)
    changed = MMRState(
        leaf_count=3,
        peaks=(state.peaks[0], Peak(0, _digest(12345))),
    )
    assert calculate_root(state) != calculate_root(changed)


def test_calculate_root_rejects_invalid_state():
    with pytest.raises(InvalidTypeError):
        calculate_root(object())


def test_append_leaf_with_nodes_reports_every_merge_bottom_up():
    from engram.commitment.mmr import append_leaf_with_nodes

    state = _state_after(7)
    result = append_leaf_with_nodes(state, _digest(8))

    assert result.state.leaf_count == 8
    assert tuple(peak.height for peak in result.state.peaks) == (3,)
    assert tuple(
        (node.start_leaf, node.height) for node in result.created_nodes
    ) == ((6, 1), (4, 2), (0, 3))
    assert state.leaf_count == 7


def test_append_leaf_with_nodes_reports_no_internal_nodes_without_merge():
    from engram.commitment.mmr import append_leaf_with_nodes

    result = append_leaf_with_nodes(MMRState(0, ()), _digest(1))
    assert result.state.leaf_count == 1
    assert result.created_nodes == ()


def test_append_leaf_uses_same_state_result_as_append_leaf_with_nodes():
    from engram.commitment.mmr import append_leaf_with_nodes

    state = _state_after(5)
    version_hash = _digest(6)
    assert append_leaf(state, version_hash) == append_leaf_with_nodes(
        state, version_hash
    ).state


def test_append_leaf_with_nodes_rejects_invalid_input():
    from engram.commitment.mmr import append_leaf_with_nodes

    with pytest.raises(InvalidTypeError):
        append_leaf_with_nodes(object(), _digest(1))
    with pytest.raises((InvalidInputError, InvalidTypeError)):
        append_leaf_with_nodes(MMRState(0, ()), bytearray(32))
