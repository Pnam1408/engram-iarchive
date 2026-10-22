
from dataclasses import dataclass

from engram.challenge import (
    create_challenge,
    select_chunk_indices,
    select_shard_indices,
)
from engram.merkle import (
    FIELD_MODULUS,
    MerkleProof,
    hash_chunk,
    verify_hash_path,
)
from engram.storage import StorageManager


@dataclass(frozen=True)
class ShardWitness:
    shard_index: int
    chunk_index: int
    chunk_data: bytes
    chunk_merkle_path: MerkleProof
    shard_merkle_path: MerkleProof


@dataclass(frozen=True)
class StorageProof:
    chunk_id: str
    p: bytes
    witnesses: tuple[ShardWitness, ...]


def generate_proof(
    storage: StorageManager,
    chunk_id: str,
    p: bytes,
    total_shards: int,
    k: int,
) -> StorageProof:
    """Collect challenged chunks and their two Merkle paths."""
    if not isinstance(p, bytes) or len(p) != 32:
        raise ValueError("p must be a 32-byte SHA-256 digest")

    if storage.get_chunk_metadata(chunk_id).total_shards != total_shards:
        raise ValueError("total_shards does not match stored metadata")

    chunks_per_shard = storage.get_chunk_counts(chunk_id)

    if len(chunks_per_shard) != total_shards:
        raise ValueError("Stored shard count does not match total_shards")

    shard_indices = select_shard_indices(
        p=p,
        total_shards=total_shards,
        k=k,
    )

    chunk_indices = select_chunk_indices(
        p=p,
        shard_indices=shard_indices,
        chunks_per_shard=chunks_per_shard,
    )

    witnesses = []

    for shard_index, chunk_index in zip(shard_indices, chunk_indices):
        (
            chunk_data,
            chunk_merkle_path,
            shard_merkle_path,
        ) = storage.get_chunk_response(
            chunk_id,
            shard_index,
            chunk_index,
        )

        witnesses.append(
            ShardWitness(
                shard_index=shard_index,
                chunk_index=chunk_index,
                chunk_data=chunk_data,
                chunk_merkle_path=chunk_merkle_path,
                shard_merkle_path=shard_merkle_path,
            )
        )

    return StorageProof(
        chunk_id=chunk_id,
        p=p,
        witnesses=tuple(witnesses),
    )


def verify_storage_proof(
    proof: StorageProof,
    expected_root: int,
    expected_p: bytes,
    total_shards: int,
    k: int,
    chunks_per_shard: tuple[int, ...],
) -> bool:
    """
    Verify the simulated two-level Merkle proof.

    expected_root is the global Merkle root over shard roots.
    chunks_per_shard is the trusted layout associated with the stored chunk.
    """
    if not isinstance(proof, StorageProof):
        return False

    if not isinstance(expected_p, bytes) or len(expected_p) != 32:
        return False

    if proof.p != expected_p:
        return False

    if (
        not isinstance(expected_root, int)
        or isinstance(expected_root, bool)
        or not 0 <= expected_root < FIELD_MODULUS
    ):
        return False

    if (
        not isinstance(total_shards, int)
        or isinstance(total_shards, bool)
        or total_shards <= 0
    ):
        return False

    if (
        not isinstance(chunks_per_shard, tuple)
        or len(chunks_per_shard) != total_shards
        or any(
            not isinstance(n, int)
            or isinstance(n, bool)
            or n <= 0
            for n in chunks_per_shard
        )
    ):
        return False

    try:
        expected_shards = select_shard_indices(
            expected_p,
            total_shards,
            k,
        )
        expected_chunks = select_chunk_indices(
            expected_p,
            expected_shards,
            chunks_per_shard,
        )
    except (ValueError, TypeError, OverflowError):
        return False

    if len(proof.witnesses) != k:
        return False

    actual_pairs = tuple(
        (w.shard_index, w.chunk_index)
        for w in proof.witnesses
    )
    expected_pairs = tuple(zip(expected_shards, expected_chunks))

    if actual_pairs != expected_pairs:
        return False

    for witness in proof.witnesses:
        if not isinstance(witness, ShardWitness):
            return False

        if not isinstance(witness.chunk_data, bytes):
            return False

        if not witness.chunk_data or len(witness.chunk_data) > 31:
            return False

        if witness.chunk_index >= chunks_per_shard[witness.shard_index]:
            return False

        chunk_path = witness.chunk_merkle_path
        shard_path = witness.shard_merkle_path

        if not isinstance(chunk_path, MerkleProof):
            return False

        if not isinstance(shard_path, MerkleProof):
            return False

        if chunk_path.leaf_index != witness.chunk_index:
            return False

        if chunk_path.leaf_count != chunks_per_shard[witness.shard_index]:
            return False

        if shard_path.leaf_index != witness.shard_index:
            return False

        if shard_path.leaf_count != total_shards:
            return False

        # Reconstruct the selected shard's chunk-tree root.
        chunk_root = _root_from_path(
            hash_chunk(witness.chunk_data),
            chunk_path,
        )
        if chunk_root is None:
            return False

        # Verify that this shard root belongs to the global tree.
        if not verify_hash_path(
            chunk_root,
            shard_path,
            expected_root,
        ):
            return False

    return True


def _root_from_path(
    leaf_hash: int,
    proof: MerkleProof,
) -> int | None:
    """
    Reconstruct a Merkle root while checking path structure.

    Assumes the tree duplicates the final node when a level has
    an odd number of nodes, as in the current Merkle implementation.
    """
    if not isinstance(proof, MerkleProof):
        return None

    index = proof.leaf_index
    width = proof.leaf_count

    if (
        not isinstance(index, int)
        or isinstance(index, bool)
        or not isinstance(width, int)
        or isinstance(width, bool)
        or width <= 0
        or not 0 <= index < width
    ):
        return None

    if (
        not isinstance(leaf_hash, int)
        or isinstance(leaf_hash, bool)
        or not 0 <= leaf_hash < FIELD_MODULUS
    ):
        return None

    current = leaf_hash
    siblings = proof.siblings

    # A Merkle path has exactly one sibling per tree level.
    expected_depth = 0
    remaining = width
    while remaining > 1:
        expected_depth += 1
        remaining = (remaining + 1) // 2

    if len(siblings) != expected_depth:
        return None

    for sibling_hash, sibling_is_left in siblings:
        if (
            not isinstance(sibling_hash, int)
            or isinstance(sibling_hash, bool)
            or not 0 <= sibling_hash < FIELD_MODULUS
            or not isinstance(sibling_is_left, bool)
        ):
            return None

        # Check that the direction matches the current leaf position.
        expected_is_left = (index % 2 == 1)
        if sibling_is_left != expected_is_left:
            return None

        # For an unpaired final node, the implementation duplicates
        # that node as its own sibling.
        if index % 2 == 0 and index == width - 1 and width % 2 == 1:
            if sibling_hash != current:
                return None

        if sibling_is_left:
            current = _hash_parent_checked(sibling_hash, current)
        else:
            current = _hash_parent_checked(current, sibling_hash)

        index //= 2
        width = (width + 1) // 2

    return current


def _hash_parent_checked(left: int, right: int) -> int:
    from engram.merkle import hash_parent

    return hash_parent(left, right)
