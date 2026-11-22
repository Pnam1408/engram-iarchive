#!/usr/bin/env python3
"""Generate ENGRAM P5.5 shard-commitment vectors using the production Python code.

Run from the repository root after installing/building native/fast_poseidon:
    python tools/shard_merkle_vectors/generate_vectors.py vectors.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from engram.commitment.shard_merkle import ShardCommitmentService


def deterministic_shards(shard_count: int, shard_size: int) -> list[bytes]:
    return [
        bytes((shard_index * 37 + offset * 13 + 7) % 256 for offset in range(shard_size))
        for shard_index in range(shard_count)
    ]


def as_strings(values):
    return [str(value) for value in values]


def make_case(name: str, shard_count: int, shard_size: int) -> dict:
    shards = deterministic_shards(shard_count, shard_size)
    commitment = ShardCommitmentService.build(shards)

    shard_items = []
    for shard_index, (shard_bytes, tree) in enumerate(zip(shards, commitment.shard_trees)):
        proofs = []
        for chunk_index in range(tree.chunk_count):
            proof = commitment.proof(shard_index, chunk_index)
            proofs.append({
                "chunk_index": chunk_index,
                "chunk_hex": proof.chunk_proof.chunk.hex(),
                "chunk_siblings": as_strings(proof.chunk_proof.siblings),
                "shard_index": proof.shard_proof.shard_index,
                "shard_siblings": as_strings(proof.shard_proof.siblings),
                "verifies": ShardCommitmentService.verify(proof, commitment.global_root),
            })
        shard_items.append({
            "shard_index": shard_index,
            "data_hex": shard_bytes.hex(),
            "chunks_hex": [chunk.hex() for chunk in tree.chunks],
            "chunk_leaf_hashes": as_strings(tree.leaf_hashes),
            "inner_levels": [as_strings(level) for level in tree.levels],
            "shard_root": str(tree.root),
            "proofs": proofs,
        })

    global_tree = commitment.global_tree
    return {
        "name": name,
        "shard_count": shard_count,
        "shard_size_bytes": shard_size,
        "chunk_size_bytes": 31,
        "shards": shard_items,
        "global_leaf_hashes": as_strings(global_tree.leaf_hashes),
        "global_levels": [as_strings(level) for level in global_tree.levels],
        "global_root": str(commitment.global_root),
    }


def main() -> None:
    output = Path(sys.argv[1] if len(sys.argv) > 1 else "shard_merkle_vectors.json")
    # Every Generation has equal-size shards. Cases cover exact and partial
    # 31-byte chunk boundaries, odd inner widths, and odd outer widths.
    cases = [
        make_case("two_shards_31_bytes", 2, 31),
        make_case("two_shards_32_bytes", 2, 32),
        make_case("three_shards_63_bytes", 3, 63),
        make_case("five_shards_64_bytes", 5, 64),
    ]
    document = {
        "protocol": "ENGRAM-SHARD-MERKLE/1",
        "field": "BN254 scalar field",
        "poseidon_arity": 3,
        "chunk_size_bytes": 31,
        "domain_tags": {
            "chunk_leaf": 1,
            "parent_node": 2,
            "shard_root": 3,
            "global_shard_leaf": 4,
            "global_root": 5,
        },
        "cases": cases,
    }
    output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(cases)} cases to {output.resolve()}")
    print("Global roots:")
    for case in cases:
        print(f"  {case['name']}: {case['global_root']}")


if __name__ == "__main__":
    main()
