import pytest

from engram.commitment.shard_merkle import tree
from engram.commitment.common.exceptions import InvalidInputError, InvalidTypeError, ValueOutOfRangeError


def _fake_hash_chunk(data: bytes) -> int:
    return int.from_bytes(data, "big") + len(data) * 1000


def _fake_hash_parent(left: int, right: int) -> int:
    # Deterministic ordered stand-in for structural tests; no real crypto claim.
    return (left * 131 + right * 17 + 7) % (2**251)


def _fake_poseidon_hash_3(a: int, b: int, c: int) -> int:
    return (a * 101 + b * 103 + c * 107 + 13) % (2**251)


@pytest.fixture(autouse=True)
def fake_hashes(monkeypatch):
    monkeypatch.setattr(tree, "hash_chunk", _fake_hash_chunk)
    monkeypatch.setattr(tree, "hash_parent", _fake_hash_parent)
    monkeypatch.setattr(tree, "poseidon_hash_3", _fake_poseidon_hash_3)


def test_single_chunk_tree_and_proof():
    built = tree.build_shard_tree(b"hello")
    assert built.chunk_count == 1
    assert built.levels == (built.leaf_hashes,)
    assert built.root != built.leaf_hashes[0]
    proof = built.proof(0)
    assert proof.siblings == ()
    assert tree.verify_chunk_proof(proof, built.root)


@pytest.mark.parametrize("count", [2, 3, 4, 5, 6, 7, 8, 9])
def test_all_chunk_proofs_for_various_tree_widths(count):
    data = b"".join(bytes([i + 1]) * 31 for i in range(count))
    built = tree.build_shard_tree(data)
    for index in range(count):
        assert tree.verify_chunk_proof(built.proof(index), built.root)


def test_partial_last_chunk_preserved():
    data = b"a" * 31 + b"xyz"
    built = tree.build_shard_tree(data)
    assert built.chunks == (b"a" * 31, b"xyz")
    assert tree.verify_chunk_proof(built.proof(1), built.root)


def test_proof_rejects_tampered_chunk_and_wrong_root():
    built = tree.build_shard_tree(b"abcdefghij" * 5)
    proof = built.proof(1)
    altered = tree.ChunkMerkleProof(
        chunk_index=proof.chunk_index,
        chunk_count=proof.chunk_count,
        chunk=proof.chunk + b"x",
        siblings=proof.siblings,
    )
    assert not tree.verify_chunk_proof(altered, built.root)
    assert not tree.verify_chunk_proof(proof, built.root + 1)


def test_build_rejects_empty_shard_and_non_bytes():
    with pytest.raises(InvalidInputError):
        tree.build_shard_tree(b"")
    with pytest.raises(InvalidTypeError):
        tree.build_shard_tree(bytearray(b"abc"))


def test_proof_index_validation():
    built = tree.build_shard_tree(b"abc")
    with pytest.raises(ValueOutOfRangeError):
        built.proof(-1)
    with pytest.raises(ValueOutOfRangeError):
        built.proof(1)
    with pytest.raises(InvalidTypeError):
        built.proof(True)


def test_verifier_rejects_malformed_proof_shape():
    built = tree.build_shard_tree(b"x" * 70)
    proof = built.proof(0)
    malformed = tree.ChunkMerkleProof(
        chunk_index=proof.chunk_index,
        chunk_count=proof.chunk_count,
        chunk=proof.chunk,
        siblings=proof.siblings[:-1],
    )
    with pytest.raises(InvalidInputError):
        tree.verify_chunk_proof(malformed, built.root)


def test_root_binds_chunk_count_to_prevent_duplicate_last_shape_ambiguity():
    chunk_a, chunk_b, chunk_c = b"a" * 31, b"b" * 31, b"c" * 31
    three = tree.build_shard_tree(chunk_a + chunk_b + chunk_c)
    four_with_duplicate_tail = tree.build_shard_tree(chunk_a + chunk_b + chunk_c + chunk_c)
    assert three.root != four_with_duplicate_tail.root
