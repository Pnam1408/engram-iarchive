import pytest

from engram.commitment.shard_merkle import service as svc
from engram.commitment.shard_merkle import tree as inner
from engram.commitment.shard_merkle import global_tree as outer
from engram.commitment.common.exceptions import InvalidInputError, InvalidTypeError, ValueOutOfRangeError


def fake_parent(left, right):
    return (left * 131 + right * 17 + 7) % (2**251)


def fake_poseidon(a, b, c):
    return (a * 101 + b * 103 + c * 107 + 13) % (2**251)


def fake_chunk(data):
    return (int.from_bytes(data, "big") * 19 + len(data) * 23 + 5) % (2**251)


@pytest.fixture(autouse=True)
def fake_hashes(monkeypatch):
    monkeypatch.setattr(inner, "hash_parent", fake_parent)
    monkeypatch.setattr(inner, "hash_chunk", fake_chunk)
    monkeypatch.setattr(inner, "poseidon_hash_3", fake_poseidon)
    monkeypatch.setattr(outer, "hash_parent", fake_parent)
    monkeypatch.setattr(outer, "poseidon_hash_3", fake_poseidon)
    monkeypatch.setattr(svc, "verify_chunk_proof", inner.verify_chunk_proof)
    monkeypatch.setattr(svc, "verify_shard_root_proof", outer.verify_shard_root_proof)


def build_example():
    return svc.ShardCommitmentService.build([b"shard-0 data", b"shard-1 data", b"third shard"])



def test_build_commitment_and_properties():
    commitment = build_example()
    assert commitment.shard_count == 3
    assert len(commitment.shard_roots) == 3
    assert commitment.global_root == commitment.global_tree.root
    assert commitment.shard_roots == tuple(t.root for t in commitment.shard_trees)


@pytest.mark.parametrize("shard_index,chunk_index", [(0, 0), (1, 0), (2, 0)])
def test_combined_proof_verifies(shard_index, chunk_index):
    commitment = build_example()
    proof = commitment.proof(shard_index, chunk_index)
    assert svc.ShardCommitmentService.verify(proof, commitment.global_root)



def test_proof_rejects_wrong_global_root():
    commitment = build_example()
    proof = commitment.proof(1, 0)
    assert not svc.ShardCommitmentService.verify(proof, commitment.global_root + 1)



def test_proof_rejects_changed_shard_index():
    commitment = build_example()
    proof = commitment.proof(1, 0)
    altered = svc.GenerationChunkProof(0, proof.chunk_proof, proof.shard_proof)
    assert not svc.ShardCommitmentService.verify(altered, commitment.global_root)



def test_proof_rejects_changed_chunk():
    commitment = build_example()
    proof = commitment.proof(0, 0)
    changed_chunk = inner.ChunkMerkleProof(
        proof.chunk_proof.chunk_index,
        proof.chunk_proof.chunk_count,
        b"altered chunk",
        proof.chunk_proof.siblings,
    )
    altered = svc.GenerationChunkProof(proof.shard_index, changed_chunk, proof.shard_proof)
    assert not svc.ShardCommitmentService.verify(altered, commitment.global_root)



def test_proof_rejects_mismatched_inner_and_outer_shard_index():
    commitment = build_example()
    proof = commitment.proof(1, 0)
    altered_outer = outer.ShardRootProof(0, proof.shard_proof.shard_count,
                                       proof.shard_proof.shard_root,
                                       proof.shard_proof.siblings)
    altered = svc.GenerationChunkProof(proof.shard_index, proof.chunk_proof, altered_outer)
    assert not svc.ShardCommitmentService.verify(altered, commitment.global_root)



def test_build_rejects_empty_input_and_invalid_types():
    with pytest.raises(InvalidInputError):
        svc.ShardCommitmentService.build([])
    with pytest.raises(InvalidTypeError):
        svc.ShardCommitmentService.build((x for x in [b"x"]))
    with pytest.raises(InvalidInputError):
        svc.ShardCommitmentService.build([b"", b"valid"])



def test_proof_creation_validates_indices():
    commitment = build_example()
    with pytest.raises(ValueOutOfRangeError):
        commitment.proof(-1, 0)
    with pytest.raises(ValueOutOfRangeError):
        commitment.proof(3, 0)
    with pytest.raises(ValueOutOfRangeError):
        commitment.proof(0, 99)
    with pytest.raises(InvalidTypeError):
        commitment.proof(True, 0)



def test_service_verify_rejects_wrong_proof_type():
    with pytest.raises(InvalidTypeError):
        svc.ShardCommitmentService.verify(object(), 123)

