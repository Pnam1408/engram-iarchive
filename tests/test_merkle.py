
import pytest

from engram.merkle import (
    build_merkle_tree,
    verify_proof,
)


def test_build_tree_and_verify_proof():
    shards = [f"shard-{i}".encode() for i in range(6)]

    tree = build_merkle_tree(shards)

    for index, shard in enumerate(shards):
        proof = tree.generate_proof(index)

        assert verify_proof(shard, proof, tree.root)


def test_modified_shard_fails_verification():
    shards = [b"shard-0", b"shard-1", b"shard-2"]
    tree = build_merkle_tree(shards)

    proof = tree.generate_proof(1)

    assert not verify_proof(
        b"modified-shard",
        proof,
        tree.root,
    )


def test_wrong_root_fails_verification():
    shards = [b"A", b"B", b"C", b"D"]
    tree = build_merkle_tree(shards)

    proof = tree.generate_proof(2)
    wrong_root = build_merkle_tree(
        [b"W", b"X", b"Y", b"Z"]
    ).root

    assert not verify_proof(shards[2], proof, wrong_root)


def test_odd_number_of_leaves():
    shards = [b"A", b"B", b"C", b"D", b"E"]
    tree = build_merkle_tree(shards)

    for index, shard in enumerate(shards):
        assert verify_proof(
            shard,
            tree.generate_proof(index),
            tree.root,
        )


def test_single_leaf_tree():
    shards = [b"only-shard"]
    tree = build_merkle_tree(shards)

    proof = tree.generate_proof(0)

    assert proof.siblings == ()
    assert verify_proof(shards[0], proof, tree.root)


def test_empty_tree_is_rejected():
    with pytest.raises(ValueError):
        build_merkle_tree([])


def test_invalid_leaf_index_is_rejected():
    tree = build_merkle_tree([b"A", b"B"])

    with pytest.raises(IndexError):
        tree.generate_proof(2)


def test_tampered_proof_is_rejected():
    shards = [b"A", b"B", b"C", b"D"]
    tree = build_merkle_tree(shards)

    proof = tree.generate_proof(0)
    sibling_hash, sibling_is_left = proof.siblings[0]

    # Thay đổi giá trị field của sibling hash.
    from engram.merkle import FIELD_MODULUS
    tampered_hash = (sibling_hash + 1) % FIELD_MODULUS

    tampered_proof = type(proof)(
        leaf_index=proof.leaf_index,
        leaf_count=proof.leaf_count,
        siblings=((tampered_hash, sibling_is_left),)
        + proof.siblings[1:],
    )

    assert not verify_proof(
        shards[0],
        tampered_proof,
        tree.root,
    )
