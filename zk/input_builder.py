# zk/input_builder.py

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from engram.merkle import MerkleProof
from engram.storage import StorageManager


def _validate_merkle_proof(
    proof: MerkleProof,
    expected_index: int,
) -> None:
    """Validate Merkle proof metadata and path orientation."""

    if proof.leaf_index != expected_index:
        raise ValueError(
            f"Expected leaf index {expected_index}, " f"got {proof.leaf_index}"
        )

    if proof.leaf_count <= 0:
        raise ValueError("Merkle proof leaf_count must be positive")

    if not 0 <= proof.leaf_index < proof.leaf_count:
        raise ValueError("Invalid Merkle proof leaf index")

    expected_depth = (proof.leaf_count - 1).bit_length()

    if len(proof.siblings) != expected_depth:
        raise ValueError(
            f"Expected path depth {expected_depth}, " f"got {len(proof.siblings)}"
        )

    for level, (sibling_hash, sibling_is_left) in enumerate(proof.siblings):
        if not isinstance(sibling_hash, int):
            raise TypeError("Merkle sibling hash must be an integer")

        if not isinstance(sibling_is_left, bool):
            raise TypeError("sibling_is_left must be a bool")

        # The current node is on the right if the corresponding
        # bit of its index is 1.
        expected_is_left = bool((expected_index >> level) & 1)

        if sibling_is_left != expected_is_left:
            raise ValueError(f"Invalid sibling orientation at level {level}")


def _serialize_merkle_path(
    proof: MerkleProof,
    expected_index: int,
) -> list[str]:
    """Return sibling hashes in bottom-up order."""

    _validate_merkle_proof(proof, expected_index)

    return [str(sibling_hash) for sibling_hash, _ in proof.siblings]


def build_single_chunk_input(
    storage: StorageManager,
    chunk_id: str,
    shard_index: int,
    chunk_index: int,
) -> dict[str, Any]:
    """Build Circom input for proving one chunk in one shard.

    The resulting input matches the single-target circuit:

        merkleRoot
        shardIndex
        chunkIndex
        chunkValue
        chunkLength
        chunkPath
        shardPath
    """

    if not isinstance(shard_index, int) or isinstance(shard_index, bool):
        raise TypeError("shard_index must be an integer")

    if not isinstance(chunk_index, int) or isinstance(chunk_index, bool):
        raise TypeError("chunk_index must be an integer")

    if shard_index < 0:
        raise ValueError("shard_index must be non-negative")

    if chunk_index < 0:
        raise ValueError("chunk_index must be non-negative")

    # Retrieve the requested chunk and its two Merkle paths.
    chunk_data, chunk_proof, shard_proof = storage.get_chunk_response(
        chunk_id=chunk_id,
        shard_index=shard_index,
        chunk_index=chunk_index,
    )

    if not chunk_data:
        raise ValueError("Chunk data must not be empty")

    # Retrieve the registered global Merkle root.
    merkle_root = storage.get_merkle_root(chunk_id)

    # Convert the chunk bytes to a big-endian integer,
    # matching int.from_bytes(..., "big") in merkle.py.
    chunk_value = int.from_bytes(chunk_data, "big")

    chunk_path = _serialize_merkle_path(
        chunk_proof,
        expected_index=chunk_index,
    )

    shard_path = _serialize_merkle_path(
        shard_proof,
        expected_index=shard_index,
    )

    return {
        "merkleRoot": str(merkle_root),
        "shardIndex": str(shard_index),
        "chunkIndex": str(chunk_index),
        "chunkValue": str(chunk_value),
        "chunkLength": str(len(chunk_data)),
        "chunkPath": chunk_path,
        "shardPath": shard_path,
    }


def save_circuit_input(
    circuit_input: dict[str, Any],
    output_path: str | Path,
) -> Path:
    """Save circuit input as JSON."""

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    output_path.write_text(
        json.dumps(
            circuit_input,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    return output_path


def build_circuit_metadata(
    storage: StorageManager,
    chunk_id: str,
) -> dict[str, int | str]:
    shard_bytes_len, n_shards = storage.get_shard_layout(chunk_id)

    return {
        "chunkId": chunk_id,
        "shardBytesLen": shard_bytes_len,
        "nShards": n_shards,
    }


def build_and_save_single_chunk_input(
    storage: StorageManager,
    chunk_id: str,
    shard_index: int,
    chunk_index: int,
    output_path: str | Path,
) -> Path:
    """Build and save one single-target circuit input and metadata."""

    output_path = Path(output_path)

    circuit_input = build_single_chunk_input(
        storage=storage,
        chunk_id=chunk_id,
        shard_index=shard_index,
        chunk_index=chunk_index,
    )

    metadata = build_circuit_metadata(
        storage=storage,
        chunk_id=chunk_id,
    )

    input_path = save_circuit_input(
        circuit_input=circuit_input,
        output_path=output_path,
    )

    metadata_path = input_path.with_name(f"{input_path.stem}_metadata.json")

    metadata_path.write_text(
        json.dumps(
            metadata,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    return input_path
