import pytest

from engram.commitment.common.exceptions import (
    InvalidInputError,
    InvalidTypeError,
    ValueOutOfRangeError,
)
from engram.commitment.mmr import (
    MAX_MMR_LEAF_COUNT,
    MAX_PEAK_HEIGHT,
    MMRState,
    Peak,
    validate_mmr_state,
    validate_peak,
)


def _peak(height: int, marker: int = 1) -> Peak:
    return Peak(height=height, hash=bytes([marker]) * 32)


def test_peak_accepts_supported_height_and_fixed_digest():
    peak = _peak(0)
    assert peak.height == 0
    assert peak.hash == b"\x01" * 32
    assert validate_peak(peak) is peak


@pytest.mark.parametrize("bad_height", [True, False, -1, 64, 256, 1.0, "1"])
def test_peak_rejects_invalid_height(bad_height):
    with pytest.raises((InvalidTypeError, ValueOutOfRangeError, InvalidInputError)):
        Peak(height=bad_height, hash=b"x" * 32)


@pytest.mark.parametrize("bad_hash", [bytearray(32), memoryview(bytes(32)), "x" * 32, b"x" * 31, b"x" * 33])
def test_peak_rejects_invalid_digest(bad_hash):
    with pytest.raises((InvalidTypeError, ValueOutOfRangeError)):
        Peak(height=0, hash=bad_hash)


def test_empty_mmr_state_is_valid():
    state = MMRState(leaf_count=0, peaks=())
    assert validate_mmr_state(state) is state


@pytest.mark.parametrize(
    ("leaf_count", "heights"),
    [
        (1, (0,)),
        (2, (1,)),
        (3, (1, 0)),
        (7, (2, 1, 0)),
        (8, (3,)),
        (11, (3, 1, 0)),
        (13, (3, 2, 0)),
        (MAX_MMR_LEAF_COUNT, tuple(range(MAX_PEAK_HEIGHT, -1, -1))),
    ],
)
def test_state_accepts_canonical_peak_decomposition(leaf_count, heights):
    peaks = tuple(_peak(height, index + 1) for index, height in enumerate(heights))
    state = MMRState(leaf_count=leaf_count, peaks=peaks)
    assert tuple(peak.height for peak in state.peaks) == heights


def test_state_requires_tuple_of_peaks():
    with pytest.raises(InvalidTypeError):
        MMRState(leaf_count=1, peaks=[_peak(0)])
    with pytest.raises(InvalidTypeError):
        MMRState(leaf_count=1, peaks=(object(),))


@pytest.mark.parametrize("bad_count", [True, False, -1, 1 << 64, 1.0, "1"])
def test_state_rejects_invalid_leaf_count(bad_count):
    with pytest.raises((InvalidTypeError, ValueOutOfRangeError)):
        MMRState(leaf_count=bad_count, peaks=())


@pytest.mark.parametrize(
    ("leaf_count", "heights"),
    [
        (0, (0,)),          # Empty state cannot contain peaks.
        (1, ()),            # Missing leaf peak.
        (2, (0, 0)),        # Wrong count and non-descending heights.
        (3, (0, 1)),        # Wrong left-to-right order.
        (3, (1,)),          # Missing peak.
        (4, (1, 0)),        # Heights do not match the binary decomposition.
        (5, (2,)),          # Missing right-hand peak.
        (11, (3, 0, 1)),    # Correct set of heights, wrong order.
    ],
)
def test_state_rejects_noncanonical_peak_shapes(leaf_count, heights):
    peaks = tuple(_peak(height, index + 1) for index, height in enumerate(heights))
    with pytest.raises(InvalidInputError):
        MMRState(leaf_count=leaf_count, peaks=peaks)


def test_validate_state_rejects_non_state_object():
    with pytest.raises(InvalidTypeError):
        validate_mmr_state(object())


def test_models_are_immutable():
    peak = _peak(0)
    state = MMRState(leaf_count=1, peaks=(peak,))
    with pytest.raises((AttributeError, TypeError)):
        peak.height = 1
    with pytest.raises((AttributeError, TypeError)):
        state.leaf_count = 2


def test_created_node_requires_aligned_internal_subtree():
    from engram.commitment.common.exceptions import InvalidInputError
    from engram.commitment.mmr import MMRCreatedNode

    assert MMRCreatedNode(start_leaf=4, height=2, hash=b"x" * 32)
    with pytest.raises(InvalidInputError, match="aligned"):
        MMRCreatedNode(start_leaf=2, height=2, hash=b"x" * 32)
    with pytest.raises(InvalidInputError, match="height"):
        MMRCreatedNode(start_leaf=0, height=0, hash=b"x" * 32)


def test_append_result_requires_tuple_of_created_nodes():
    from engram.commitment.common.exceptions import InvalidTypeError
    from engram.commitment.mmr import MMRAppendResult, MMRCreatedNode

    node = MMRCreatedNode(start_leaf=0, height=1, hash=b"x" * 32)
    with pytest.raises(InvalidTypeError, match="tuple"):
        MMRAppendResult(MMRState(2, (Peak(1, b"y" * 32),)), [node])
