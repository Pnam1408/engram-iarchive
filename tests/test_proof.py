
import pytest
from dataclasses import replace

from engram.challenge import (
    create_challenge,
    derive_p,
    select_chunk_indices,
)
from engram.erasure_coding import encode
from engram.merkle import hash_chunk, verify_hash_path
from engram.storage import StorageManager
from engram.proof import generate_proof, verify_storage_proof


TOTAL_SHARDS = 6
CHALLENGE_SIZE = 3


def setup_storage(tmp_path):
    storage = StorageManager(tmp_path, node_count=TOTAL_SHARDS)

    # Dữ liệu đủ lớn để mỗi shard chứa nhiều chunk.
    payload = bytes(range(256)) * 8
    encoded = encode(
        payload,
        data_shards=4,
        total_shards=TOTAL_SHARDS,
    )
    storage.store_chunk("chunk_001", encoded)

    return storage


def create_test_challenge(storage):
    chunks_per_shard = storage.get_chunk_counts("chunk_001")

    return create_challenge(
        "chunk_001",
        total_shards=TOTAL_SHARDS,
        chunks_per_shard=chunks_per_shard,
        k=CHALLENGE_SIZE,
    )


def verify_test_proof(storage, proof, root, expected_p):
    return verify_storage_proof(
        proof=proof,
        expected_root=root,
        expected_p=expected_p,
        total_shards=TOTAL_SHARDS,
        k=CHALLENGE_SIZE,
        chunks_per_shard=storage.get_chunk_counts(proof.chunk_id),
    )


def test_challenge_selects_expected_number_of_shards_and_chunks(tmp_path):
    storage = setup_storage(tmp_path)
    challenge = create_test_challenge(storage)
    chunks_per_shard = storage.get_chunk_counts("chunk_001")

    assert len(challenge.shard_indices) == CHALLENGE_SIZE
    assert len(challenge.chunk_indices) == CHALLENGE_SIZE
    assert len(set(challenge.shard_indices)) == CHALLENGE_SIZE

    for shard_index, chunk_index in zip(
        challenge.shard_indices,
        challenge.chunk_indices,
    ):
        assert 0 <= shard_index < TOTAL_SHARDS
        assert 0 <= chunk_index < chunks_per_shard[shard_index]


def test_same_inputs_produce_same_challenge(tmp_path):
    storage = setup_storage(tmp_path)

    first = create_test_challenge(storage)
    second = create_test_challenge(storage)

    assert first == second


def test_chunk_indices_are_deterministic():
    p = derive_p("block-1", "miner-1", "epoch-1")
    shards = (0, 2, 4)
    chunks_per_shard = (10, 12, 14, 16, 18, 20)

    first = select_chunk_indices(p, shards, chunks_per_shard)
    second = select_chunk_indices(p, shards, chunks_per_shard)

    assert first == second


def test_invalid_chunk_layout_is_rejected():
    p = derive_p("block-1", "miner-1", "epoch-1")

    with pytest.raises(ValueError):
        select_chunk_indices(p, (0, 1), (10, 0, 10))


def test_invalid_shard_index_is_rejected():
    p = derive_p("block-1", "miner-1", "epoch-1")

    with pytest.raises(ValueError):
        select_chunk_indices(p, (0, 6), (10, 10, 10, 10, 10, 10))


def test_valid_challenge_proof(tmp_path):
    storage = setup_storage(tmp_path)
    root = storage.get_merkle_root("chunk_001")
    challenge = create_test_challenge(storage)

    proof = generate_proof(
        storage=storage,
        chunk_id=challenge.chunk_id,
        p=challenge.p,
        total_shards=TOTAL_SHARDS,
        k=CHALLENGE_SIZE,
    )

    assert len(proof.witnesses) == CHALLENGE_SIZE
    assert verify_test_proof(storage, proof, root, challenge.p)


def test_modified_chunk_is_rejected(tmp_path):
    storage = setup_storage(tmp_path)
    root = storage.get_merkle_root("chunk_001")
    challenge = create_test_challenge(storage)

    proof = generate_proof(
        storage=storage,
        chunk_id=challenge.chunk_id,
        p=challenge.p,
        total_shards=TOTAL_SHARDS,
        k=CHALLENGE_SIZE,
    )

    first = proof.witnesses[0]
    modified_data = bytes([first.chunk_data[0] ^ 1]) + first.chunk_data[1:]

    modified_witness = replace(first, chunk_data=modified_data)
    modified_proof = replace(
        proof,
        witnesses=(modified_witness,) + proof.witnesses[1:],
    )

    assert not verify_test_proof(
        storage, modified_proof, root, challenge.p
    )


def test_wrong_challenge_value_is_rejected(tmp_path):
    storage = setup_storage(tmp_path)
    root = storage.get_merkle_root("chunk_001")
    challenge = create_test_challenge(storage)

    proof = generate_proof(
        storage=storage,
        chunk_id=challenge.chunk_id,
        p=challenge.p,
        total_shards=TOTAL_SHARDS,
        k=CHALLENGE_SIZE,
    )

    modified_proof = replace(proof, p=b"\x00" * 32)

    assert not verify_test_proof(
        storage, modified_proof, root, challenge.p
    )


def test_wrong_shard_index_is_rejected(tmp_path):
    storage = setup_storage(tmp_path)
    root = storage.get_merkle_root("chunk_001")
    challenge = create_test_challenge(storage)

    proof = generate_proof(
        storage=storage,
        chunk_id=challenge.chunk_id,
        p=challenge.p,
        total_shards=TOTAL_SHARDS,
        k=CHALLENGE_SIZE,
    )

    first = proof.witnesses[0]
    wrong_index = (first.shard_index + 1) % TOTAL_SHARDS
    modified_witness = replace(first, shard_index=wrong_index)

    modified_proof = replace(
        proof,
        witnesses=(modified_witness,) + proof.witnesses[1:],
    )

    assert not verify_test_proof(
        storage, modified_proof, root, challenge.p
    )


def test_wrong_chunk_index_is_rejected(tmp_path):
    storage = setup_storage(tmp_path)
    root = storage.get_merkle_root("chunk_001")
    challenge = create_test_challenge(storage)

    proof = generate_proof(
        storage=storage,
        chunk_id=challenge.chunk_id,
        p=challenge.p,
        total_shards=TOTAL_SHARDS,
        k=CHALLENGE_SIZE,
    )

    first = proof.witnesses[0]
    chunk_count = storage.get_chunk_counts("chunk_001")[first.shard_index]
    wrong_index = (first.chunk_index + 1) % chunk_count

    modified_witness = replace(first, chunk_index=wrong_index)
    modified_proof = replace(
        proof,
        witnesses=(modified_witness,) + proof.witnesses[1:],
    )

    assert not verify_test_proof(
        storage, modified_proof, root, challenge.p
    )


def test_chunk_merkle_path_is_valid(tmp_path):
    storage = setup_storage(tmp_path)
    root = storage.get_merkle_root("chunk_001")
    challenge = create_test_challenge(storage)

    proof = generate_proof(
        storage=storage,
        chunk_id=challenge.chunk_id,
        p=challenge.p,
        total_shards=TOTAL_SHARDS,
        k=CHALLENGE_SIZE,
    )

    for witness in proof.witnesses:
        chunk_root = _reconstruct_root_for_test(
            hash_chunk(witness.chunk_data),
            witness.chunk_merkle_path,
        )

        assert chunk_root is not None
        assert verify_hash_path(
            chunk_root,
            witness.shard_merkle_path,
            root,
        )


def _reconstruct_root_for_test(leaf_hash, proof):
    """Independent helper for testing the two-level Merkle paths."""
    from engram.merkle import FIELD_MODULUS, hash_parent

    index = proof.leaf_index
    width = proof.leaf_count
    current = leaf_hash

    if width <= 0 or not 0 <= index < width:
        return None

    expected_depth = 0
    remaining = width
    while remaining > 1:
        expected_depth += 1
        remaining = (remaining + 1) // 2

    if len(proof.siblings) != expected_depth:
        return None

    for sibling_hash, sibling_is_left in proof.siblings:
        if not 0 <= sibling_hash < FIELD_MODULUS:
            return None

        if sibling_is_left != (index % 2 == 1):
            return None

        if index % 2 == 0 and index == width - 1 and width % 2 == 1:
            if sibling_hash != current:
                return None

        if sibling_is_left:
            current = hash_parent(sibling_hash, current)
        else:
            current = hash_parent(current, sibling_hash)

        index //= 2
        width = (width + 1) // 2

    return current


def test_unavailable_node_cannot_provide_proof(tmp_path):
    storage = setup_storage(tmp_path)
    challenge = create_test_challenge(storage)

    selected_index = challenge.shard_indices[0]
    storage.set_node_available(selected_index, False)

    with pytest.raises(ConnectionError):
        generate_proof(
            storage=storage,
            chunk_id=challenge.chunk_id,
            p=challenge.p,
            total_shards=TOTAL_SHARDS,
            k=CHALLENGE_SIZE,
        )
