import pytest

from engram.commitment.shard_merkle import global_tree as gt
from engram.commitment.common.exceptions import InvalidInputError, InvalidTypeError, ValueOutOfRangeError



def _fake_parent(left, right):
    return (left * 131 + right * 17 + 7) % (2**251)


def _fake_poseidon(a, b, c):
    return (a * 101 + b * 103 + c * 107 + 13) % (2**251)


@pytest.fixture(autouse=True)
def fake_hashes(monkeypatch):
    monkeypatch.setattr(gt, "hash_parent", _fake_parent)
    monkeypatch.setattr(gt, "poseidon_hash_3", _fake_poseidon)


def test_single_shard_global_tree_and_proof():
    tree = gt.build_global_tree([123])
    assert tree.shard_count == 1
    assert tree.levels == (tree.leaf_hashes,)
    proof = tree.proof(0)
    assert proof.siblings == ()
    assert gt.verify_shard_root_proof(proof, tree.root)


@pytest.mark.parametrize("count", range(2, 11))
def test_all_proofs_verify_for_various_widths(count):
    roots = [i * 17 + 5 for i in range(count)]
    tree = gt.build_global_tree(roots)
    assert tree.shard_count == count
    for i in range(count):
        assert gt.verify_shard_root_proof(tree.proof(i), tree.root)


def test_proof_binds_index_and_shard_root():
    tree = gt.build_global_tree([101, 202, 303])
    proof = tree.proof(1)
    changed_index = gt.ShardRootProof(0, proof.shard_count, proof.shard_root, proof.siblings)
    changed_root = gt.ShardRootProof(proof.shard_index, proof.shard_count, 999, proof.siblings)
    assert not gt.verify_shard_root_proof(changed_index, tree.root)
    assert not gt.verify_shard_root_proof(changed_root, tree.root)


def test_global_root_binds_shard_count():
    roots = [11, 22, 33]
    three = gt.build_global_tree(roots)
    four_duplicate_tail = gt.build_global_tree([11, 22, 33, 33])
    assert three.root != four_duplicate_tail.root


def test_empty_and_wrong_input_types_rejected():
    with pytest.raises(InvalidInputError):
        gt.build_global_tree([])
    with pytest.raises(InvalidTypeError):
        gt.build_global_tree((x for x in [1, 2]))
    with pytest.raises(InvalidTypeError):
        gt.build_global_tree([True])
    with pytest.raises(ValueOutOfRangeError):
        gt.build_global_tree([-1])
    with pytest.raises(ValueOutOfRangeError):
        gt.build_global_tree([gt.FIELD_MODULUS])


def test_proof_index_validation():
    tree = gt.build_global_tree([10, 20])
    with pytest.raises(ValueOutOfRangeError):
        tree.proof(-1)
    with pytest.raises(ValueOutOfRangeError):
        tree.proof(2)
    with pytest.raises(InvalidTypeError):
        tree.proof(True)


def test_wrong_global_root_returns_false():
    tree = gt.build_global_tree([1, 2, 3, 4])
    assert not gt.verify_shard_root_proof(tree.proof(2), tree.root + 1)


def test_malformed_proof_depth_rejected():
    tree = gt.build_global_tree([1, 2, 3, 4, 5])
    proof = tree.proof(0)
    malformed = gt.ShardRootProof(proof.shard_index, proof.shard_count, proof.shard_root, proof.siblings[:-1])
    with pytest.raises(InvalidInputError):
        gt.verify_shard_root_proof(malformed, tree.root)


def test_odd_last_leaf_must_duplicate_itself():
    tree = gt.build_global_tree([1, 2, 3])
    proof = tree.proof(2)
    assert gt.verify_shard_root_proof(proof, tree.root)
    bad_siblings = list(proof.siblings)
    bad_siblings[0] = 987654
    malformed = gt.ShardRootProof(proof.shard_index, proof.shard_count, proof.shard_root, tuple(bad_siblings))
    assert not gt.verify_shard_root_proof(malformed, tree.root)
