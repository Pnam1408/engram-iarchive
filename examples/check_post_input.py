"""Check that zk/input.json matches the synthetic two-level Merkle witness."""
from __future__ import annotations

import json
from pathlib import Path

from engram.merkle import CHUNK_BYTES, build_chunk_merkle_tree, build_hash_tree, hash_chunk, verify_hash_path
from zk.generate_post_input import make_post_input


def main() -> None:
    shards = [bytes([i]) * 512 for i in range(1, 9)]
    indices = [1, 4, 6]
    chunk_indices = [0, 7, 16]
    input_path = Path("zk/input.json")
    actual = json.loads(input_path.read_text(encoding="utf-8"))
    expected = make_post_input(shards, indices, chunk_indices)
    if actual != expected:
        raise SystemExit("[FAIL] input.json does not match generated chunk-level witness")
    print("[PASS] input.json matches the chunk-level Merkle witness")

    chunk_trees = [build_chunk_merkle_tree(shard) for shard in shards]
    outer_tree = build_hash_tree([tree.root for tree in chunk_trees])
    for shard_index, chunk_index in zip(indices, chunk_indices):
        chunk = shards[shard_index][chunk_index * CHUNK_BYTES:(chunk_index + 1) * CHUNK_BYTES]
        chunk_proof = chunk_trees[shard_index].generate_proof(chunk_index)
        shard_proof = outer_tree.generate_proof(shard_index)
        assert verify_hash_path(hash_chunk(chunk), chunk_proof, chunk_trees[shard_index].root)
        assert verify_hash_path(chunk_trees[shard_index].root, shard_proof, outer_tree.root)
    print("[PASS] All selected chunk and shard Merkle paths verify")


if __name__ == "__main__":
    main()
