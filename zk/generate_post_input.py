
import json
from pathlib import Path

from engram.merkle import (
    CHUNK_BYTES,
    build_chunk_merkle_tree,
    build_hash_tree,
)


def make_post_input(
    shards: list[bytes],
    indices: list[int],
    chunk_indices: list[int],
) -> dict:
    """Generate witness input for the two-level EngramPoSt circuit."""
    if len(shards) != 8:
        raise ValueError("This circuit expects exactly 8 shards")

    if not shards or any(not isinstance(shard, bytes) or not shard for shard in shards):
        raise ValueError("Every shard must be non-empty bytes")

    if len({len(shard) for shard in shards}) != 1:
        raise ValueError("All shards must have the same byte length")

    if len(indices) != 3 or len(set(indices)) != 3:
        raise ValueError("Provide 3 distinct shard indices")

    if any(not isinstance(i, int) or not 0 <= i < len(shards) for i in indices):
        raise ValueError("Shard index out of range")

    if len(chunk_indices) != 3:
        raise ValueError("Provide one chunk index per selected shard")

    shard_bytes_len = len(shards[0])
    n_chunks = (shard_bytes_len + CHUNK_BYTES - 1) // CHUNK_BYTES

    # Construct one chunk tree per shard.
    chunk_trees = [
        build_chunk_merkle_tree(shard)
        for shard in shards
    ]

    # The global tree commits to the per-shard chunk-tree roots.
    global_tree = build_hash_tree([
        tree.root for tree in chunk_trees
    ])

    chunk_values = []
    chunk_lengths = []
    chunk_paths = []
    shard_paths = []

    for shard_index, chunk_index in zip(indices, chunk_indices):
        chunks = [
            shards[shard_index][i:i + CHUNK_BYTES]
            for i in range(0, shard_bytes_len, CHUNK_BYTES)
        ]

        if not isinstance(chunk_index, int) or not 0 <= chunk_index < len(chunks):
            raise ValueError(
                f"Chunk index {chunk_index} is invalid for shard {shard_index}"
            )

        chunk = chunks[chunk_index]

        chunk_values.append(str(int.from_bytes(chunk, "big")))
        chunk_lengths.append(str(len(chunk)))

        chunk_proof = chunk_trees[shard_index].generate_proof(chunk_index)
        chunk_paths.append([
            str(sibling_hash)
            for sibling_hash, _ in chunk_proof.siblings
        ])

        shard_proof = global_tree.generate_proof(shard_index)
        shard_paths.append([
            str(sibling_hash)
            for sibling_hash, _ in shard_proof.siblings
        ])

    return {
        "nShards": len(shards),
        "shardBytesLen": len(shards[0]),
    
        "merkleRoot": str(global_tree.root),
        "idx0": str(indices[0]),
        "idx1": str(indices[1]),
        "idx2": str(indices[2]),
        "chunkIdx0": str(chunk_indices[0]),
        "chunkIdx1": str(chunk_indices[1]),
        "chunkIdx2": str(chunk_indices[2]),
        "chunkValues": chunk_values,
        "chunkLengths": chunk_lengths,
        "chunkPaths": chunk_paths,
        "shardPaths": shard_paths,
    }


def save_post_input(
    shards: list[bytes],
    indices: list[int],
    chunk_indices: list[int],
    output_path: str | Path,
) -> None:
    data = make_post_input(shards, indices, chunk_indices)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(data, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    # Example: 8 shards, each 512 bytes -> 17 chunks per shard.
    shards = [
        bytes([i]) * 512
        for i in range(1, 9)
    ]

    save_post_input(
        shards=shards,
        indices=[1, 4, 6],
        chunk_indices=[0, 7, 16],
        output_path="zk/input.json",
    )

    print("Created zk/input.json")
