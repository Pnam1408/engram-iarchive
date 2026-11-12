from __future__ import annotations
import copy
import pytest

from engram.mmr import (
    MMRAccumulator,
    generate_inclusion_proof,
    verify_inclusion_proof,
    generate_consistency_proof,
    verify_consistency_proof,
)


def build_mmr(count: int) -> MMRAccumulator:
    mmr = MMRAccumulator()
    for version in range(1, count + 1):
        mmr.append(
            {"object_id": "doc", "version": version, "delta_sha256": f"{version:064x}"},
            version=version,
        )
    return mmr


@pytest.mark.parametrize("count", [1, 2, 3, 4, 7, 8, 13, 17])
def test_inclusion_proofs_for_every_leaf(count: int):
    mmr = build_mmr(count)
    for index in range(count):
        proof = generate_inclusion_proof(mmr, index)
        assert verify_inclusion_proof(proof, mmr.root)


def test_inclusion_rejects_tampered_leaf_sibling_peak_and_index():
    mmr = build_mmr(13)
    proof = generate_inclusion_proof(mmr, 9)
    changed = copy.deepcopy(proof)
    changed["leaf_hash"] = "00" * 32
    assert not verify_inclusion_proof(changed, mmr.root)
    changed = copy.deepcopy(proof)
    changed["siblings"][0]["hash"] = "11" * 32
    assert not verify_inclusion_proof(changed, mmr.root)
    changed = copy.deepcopy(proof)
    changed["peaks"][0]["hash"] = "22" * 32
    assert not verify_inclusion_proof(changed, mmr.root)
    changed = copy.deepcopy(proof)
    changed["leaf_index"] = 0
    assert not verify_inclusion_proof(changed, mmr.root)


def test_inclusion_rejects_missing_or_reordered_siblings():
    mmr = build_mmr(13)
    proof = generate_inclusion_proof(mmr, 2)
    changed = copy.deepcopy(proof)
    changed["siblings"].pop()
    assert not verify_inclusion_proof(changed, mmr.root)
    changed = copy.deepcopy(proof)
    changed["siblings"].reverse()
    assert not verify_inclusion_proof(changed, mmr.root)


@pytest.mark.parametrize(
    "old_size,new_size",
    [
        (0, 0),
        (0, 1),
        (1, 2),
        (3, 4),
        (7, 8),
        (7, 13),
        (8, 13),
        (13, 13),
        (13, 17),
        (15, 31),
    ],
)
def test_consistency_proofs(old_size: int, new_size: int):
    mmr = build_mmr(new_size)
    old_snapshot = build_mmr(old_size)
    proof = generate_consistency_proof(mmr, old_size)
    assert verify_consistency_proof(proof, old_snapshot.root, mmr.root)


def test_consistency_rejects_tampered_roots_and_subtrees():
    mmr = build_mmr(13)
    old = build_mmr(7)
    proof = generate_consistency_proof(mmr, 7)
    assert not verify_consistency_proof(proof, b"x" * 32, mmr.root)
    assert not verify_consistency_proof(proof, old.root, b"y" * 32)
    changed = copy.deepcopy(proof)
    changed["appended_subtrees"][0]["hash"] = "00" * 32
    assert not verify_consistency_proof(changed, old.root, mmr.root)
    changed = copy.deepcopy(proof)
    changed["appended_subtrees"].pop()
    assert not verify_consistency_proof(changed, old.root, mmr.root)
    changed = copy.deepcopy(proof)
    changed["appended_subtrees"].reverse()
    assert not verify_consistency_proof(changed, old.root, mmr.root)


def test_consistency_rejects_invalid_size_and_layout():
    mmr = build_mmr(5)
    proof = generate_consistency_proof(mmr, 2)
    changed = copy.deepcopy(proof)
    changed["new_size"] = 1
    assert not verify_consistency_proof(changed, build_mmr(2).root, mmr.root)
    changed = copy.deepcopy(proof)
    changed["old_peaks"][0]["height"] += 1
    assert not verify_consistency_proof(changed, build_mmr(2).root, mmr.root)


def test_generator_rejects_old_size_larger_than_current():
    with pytest.raises(ValueError):
        generate_consistency_proof(build_mmr(3), 4)
