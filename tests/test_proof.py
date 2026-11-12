from dataclasses import replace

import pytest

from engram.erasure_coding import EncodedShards
from engram.proof import (
    ShardChallenge,
    ShardWitness,
    generate_shard_proof,
    verify_shard_proof,
    verify_challenge_proofs,
)
from engram.storage import StorageManager
from engram.challenge_common import derive_p


@pytest.fixture
def proof_context(tmp_path):
    """
    Tạo một chunk gồm 3 shard, mỗi shard có 70 bytes.

    Mỗi shard có:
        ceil(70 / 31) = 3 chunks

    Cấu trúc commitment:
        global_root
          ├── shard_root_0
          │     ├── chunk_0
          │     ├── chunk_1
          │     └── chunk_2
          ├── shard_root_1
          │     ├── chunk_0
          │     ├── chunk_1
          │     └── chunk_2
          └── shard_root_2
                ├── chunk_0
                ├── chunk_1
                └── chunk_2
    """

    shards = (
        bytes(range(70)),
        bytes((i + 70) % 256 for i in range(70)),
        bytes((i + 140) % 256 for i in range(70)),
    )

    encoded = EncodedShards(
        shards=shards,
        original_size=210,
        data_shards=3,
        total_shards=3,
    )

    storage = StorageManager(
        root_dir=tmp_path / "storage",
        node_count=3,
    )

    chunk_id = "test-chunk"

    storage.store_chunk(chunk_id, encoded)

    witnesses = {}

    for shard_index in range(3):
        for chunk_index in range(3):
            chunk_data, chunk_path, shard_path = storage.get_chunk_response(
                chunk_id=chunk_id,
                shard_index=shard_index,
                chunk_index=chunk_index,
            )

            witnesses[(shard_index, chunk_index)] = ShardWitness(
                commitment_id=chunk_id,
                shard_index=shard_index,
                chunk_index=chunk_index,
                chunk_data=chunk_data,
                chunk_merkle_path=chunk_path,
                shard_merkle_path=shard_path,
            )

    return {
        "storage": storage,
        "commitment_id": chunk_id,
        "root": storage.get_merkle_root(chunk_id),
        "chunks_per_shard": storage.get_chunk_counts(chunk_id),
        "total_shards": 3,
        "witnesses": witnesses,
    }


@pytest.fixture
def challenge_p():
    return derive_p("block_hash=abc", "miner_id=miner-001", "epoch_id=epoch-001")


def make_proof(target, witness, p):
    return generate_shard_proof(
        target=target,
        p=p,
        witness=witness,
    )


def verify_single(context, target, proof, p):
    return verify_shard_proof(
        proof=proof,
        expected_root=context["root"],
        expected_target=target,
        expected_p=p,
        chunks_per_shard=context["chunks_per_shard"],
        total_shards=context["total_shards"],
    )


# ---------------------------------------------------------------------
# Single proof verification
# ---------------------------------------------------------------------


def test_verify_valid_shard_proof(proof_context, challenge_p):
    context = proof_context

    target = ShardChallenge(
        commitment_id=context["commitment_id"],
        shard_index=0,
        chunk_index=0,
    )

    witness = context["witnesses"][(0, 0)]
    proof = make_proof(target, witness, challenge_p)

    assert verify_single(context, target, proof, challenge_p)


def test_verify_valid_last_chunk(proof_context, challenge_p):
    """Kiểm tra chunk cuối cùng, có thể có độ dài nhỏ hơn 31 bytes."""

    context = proof_context

    target = ShardChallenge(
        commitment_id=context["commitment_id"],
        shard_index=0,
        chunk_index=2,
    )

    witness = context["witnesses"][(0, 2)]
    proof = make_proof(target, witness, challenge_p)

    assert len(witness.chunk_data) == 8
    assert verify_single(context, target, proof, challenge_p)


def test_verify_rejects_tampered_chunk_data(
    proof_context,
    challenge_p,
):
    context = proof_context

    target = ShardChallenge(
        context["commitment_id"],
        0,
        0,
    )

    witness = context["witnesses"][(0, 0)]

    tampered_data = bytes([witness.chunk_data[0] ^ 0xFF]) + witness.chunk_data[1:]

    tampered_witness = replace(
        witness,
        chunk_data=tampered_data,
    )

    proof = make_proof(target, tampered_witness, challenge_p)

    assert not verify_single(
        context,
        target,
        proof,
        challenge_p,
    )


def test_verify_rejects_tampered_chunk_merkle_path(
    proof_context,
    challenge_p,
):
    context = proof_context

    target = ShardChallenge(
        context["commitment_id"],
        0,
        0,
    )

    witness = context["witnesses"][(0, 0)]

    siblings = list(witness.chunk_merkle_path.siblings)

    original_hash, sibling_is_left = siblings[0]
    siblings[0] = (
        (original_hash + 1) % (2**254),
        sibling_is_left,
    )

    tampered_path = replace(
        witness.chunk_merkle_path,
        siblings=tuple(siblings),
    )

    tampered_witness = replace(
        witness,
        chunk_merkle_path=tampered_path,
    )

    proof = make_proof(target, tampered_witness, challenge_p)

    assert not verify_single(
        context,
        target,
        proof,
        challenge_p,
    )


def test_verify_rejects_tampered_shard_merkle_path(
    proof_context,
    challenge_p,
):
    context = proof_context

    target = ShardChallenge(
        context["commitment_id"],
        0,
        0,
    )

    witness = context["witnesses"][(0, 0)]

    siblings = list(witness.shard_merkle_path.siblings)

    original_hash, sibling_is_left = siblings[0]
    siblings[0] = (
        (original_hash + 1) % (2**254),
        sibling_is_left,
    )

    tampered_path = replace(
        witness.shard_merkle_path,
        siblings=tuple(siblings),
    )

    tampered_witness = replace(
        witness,
        shard_merkle_path=tampered_path,
    )

    proof = make_proof(target, tampered_witness, challenge_p)

    assert not verify_single(
        context,
        target,
        proof,
        challenge_p,
    )


def test_verify_rejects_wrong_challenge_p(
    proof_context,
    challenge_p,
):
    context = proof_context

    target = ShardChallenge(
        context["commitment_id"],
        0,
        0,
    )

    witness = context["witnesses"][(0, 0)]
    proof = make_proof(target, witness, challenge_p)

    assert not verify_single(
        context,
        target,
        proof,
        b"wrong-challenge-p",
    )


def test_verify_rejects_wrong_target(
    proof_context,
    challenge_p,
):
    context = proof_context

    original_target = ShardChallenge(
        context["commitment_id"],
        0,
        0,
    )

    wrong_target = ShardChallenge(
        context["commitment_id"],
        1,
        0,
    )

    witness = context["witnesses"][(0, 0)]
    proof = make_proof(original_target, witness, challenge_p)

    assert not verify_single(
        context,
        wrong_target,
        proof,
        challenge_p,
    )


def test_verify_rejects_wrong_commitment_root(
    proof_context,
    challenge_p,
):
    context = proof_context

    target = ShardChallenge(
        context["commitment_id"],
        0,
        0,
    )

    witness = context["witnesses"][(0, 0)]
    proof = make_proof(target, witness, challenge_p)

    wrong_root = (context["root"] + 1) % (2**254)

    assert not verify_shard_proof(
        proof=proof,
        expected_root=wrong_root,
        expected_target=target,
        expected_p=challenge_p,
        chunks_per_shard=(3,),
        total_shards=3,
    )


# ---------------------------------------------------------------------
# Batch challenge verification
# ---------------------------------------------------------------------


def test_verify_multiple_valid_proofs(
    proof_context,
    challenge_p,
):
    context = proof_context

    targets = (
        ShardChallenge(context["commitment_id"], 0, 0),
        ShardChallenge(context["commitment_id"], 1, 1),
        ShardChallenge(context["commitment_id"], 2, 2),
    )

    proofs = [
        make_proof(
            target,
            context["witnesses"][(target.shard_index, target.chunk_index)],
            challenge_p,
        )
        for target in targets
    ]

    result = verify_challenge_proofs(
        challenge_targets=targets,
        proofs=tuple(proofs),
        registered_roots={
            context["commitment_id"]: context["root"],
        },
        expected_p=challenge_p,
        layouts={
            context["commitment_id"]: context["chunks_per_shard"],
        },
    )

    assert result.valid
    assert result.verified_count == 3
    assert result.expected_count == 3
    assert result.failed_targets == ()


def test_batch_verification_rejects_missing_proof(
    proof_context,
    challenge_p,
):
    context = proof_context

    targets = (
        ShardChallenge(context["commitment_id"], 0, 0),
        ShardChallenge(context["commitment_id"], 1, 1),
    )

    proofs = [
        make_proof(
            targets[0],
            context["witnesses"][(0, 0)],
            challenge_p,
        )
    ]

    result = verify_challenge_proofs(
        challenge_targets=targets,
        proofs=tuple(proofs),
        registered_roots={
            context["commitment_id"]: context["root"],
        },
        expected_p=challenge_p,
        layouts={
            context["commitment_id"]: context["chunks_per_shard"],
        },
    )

    assert not result.valid
    assert result.verified_count < result.expected_count


def test_batch_verification_rejects_duplicate_proof(
    proof_context,
    challenge_p,
):
    context = proof_context

    target = ShardChallenge(
        context["commitment_id"],
        0,
        0,
    )

    witness = context["witnesses"][(0, 0)]
    proof = make_proof(target, witness, challenge_p)

    result = verify_challenge_proofs(
        challenge_targets=(target,),
        proofs=(proof, proof),
        registered_roots={
            context["commitment_id"]: context["root"],
        },
        expected_p=challenge_p,
        layouts={
            context["commitment_id"]: context["chunks_per_shard"],
        },
    )

    assert not result.valid


def test_batch_verification_rejects_unexpected_proof(
    proof_context,
    challenge_p,
):
    context = proof_context

    expected_target = ShardChallenge(
        context["commitment_id"],
        0,
        0,
    )

    unexpected_target = ShardChallenge(
        context["commitment_id"],
        1,
        1,
    )

    expected_proof = make_proof(
        expected_target,
        context["witnesses"][(0, 0)],
        challenge_p,
    )

    unexpected_proof = make_proof(
        unexpected_target,
        context["witnesses"][(1, 1)],
        challenge_p,
    )

    result = verify_challenge_proofs(
        challenge_targets=(expected_target,),
        proofs=(expected_proof, unexpected_proof),
        registered_roots={
            context["commitment_id"]: context["root"],
        },
        expected_p=challenge_p,
        layouts={
            context["commitment_id"]: context["chunks_per_shard"],
        },
    )

    assert not result.valid


def test_batch_verification_rejects_unknown_commitment(
    challenge_p,
):
    target = ShardChallenge(
        commitment_id="unknown-generation",
        shard_index=0,
        chunk_index=0,
    )

    result = verify_challenge_proofs(
        challenge_targets=(target,),
        proofs=(),
        registered_roots={},
        expected_p=challenge_p,
        layouts={},
    )

    assert not result.valid
