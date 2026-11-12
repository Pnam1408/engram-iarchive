from __future__ import annotations
import copy, pytest
from engram.mmr import MMRAccumulator, canonical_manifest_bytes, hash_node
from engram.mmr.peaks import peak_layout, peak_heights


@pytest.mark.parametrize(
    "count,heights",
    [
        (0, []),
        (1, [0]),
        (2, [1]),
        (3, [1, 0]),
        (4, [2]),
        (7, [2, 1, 0]),
        (8, [3]),
        (13, [3, 2, 0]),
    ],
)
def test_peak_decomposition(count, heights):
    assert peak_heights(count) == heights
    assert [h for _, h in peak_layout(count)] == heights
    assert sum(1 << h for h in heights) == count


def test_append_and_json_snapshot_roundtrip():
    m = MMRAccumulator()
    roots = []
    for v in range(1, 14):
        roots.append(m.append({"object_id": "doc", "version": v}, version=v))
    assert m.leaf_count == 13 and m.last_version == 13
    assert [p.height for p in m.peaks] == [3, 2, 0]
    restored = MMRAccumulator.from_dict(m.to_dict())
    assert restored.root == m.root and restored.to_dict() == m.to_dict()
    assert len(set(roots)) == len(roots)


def test_reject_non_contiguous_versions():
    with pytest.raises(ValueError):
        MMRAccumulator().append({"version": 2}, version=2)


def test_canonical_json_stable_and_unicode_nfc():
    assert canonical_manifest_bytes(
        {"name": "e\u0301", "version": 1}
    ) == canonical_manifest_bytes({"version": 1, "name": "é"})


def test_canonical_json_rejects_float():
    with pytest.raises(TypeError):
        canonical_manifest_bytes({"v": 1.5})


def test_hash_node_requires_sha256_digest_size():
    with pytest.raises(ValueError):
        hash_node(b"short", b"x" * 32)


def test_snapshot_tampering_detected():
    m = MMRAccumulator()
    m.append({"version": 1}, version=1)
    state = copy.deepcopy(m.to_dict())
    state["root"] = "00" * 32
    with pytest.raises(ValueError, match="root metadata mismatch"):
        MMRAccumulator.from_dict(state)
