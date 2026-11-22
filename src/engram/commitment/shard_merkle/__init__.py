"""Shard Merkle commitment primitives."""

from .hashing import (
    CHUNK_BYTES,
    FIELD_MODULUS,
    POSEIDON_ARITY,
    hash_chunk,
    hash_parent,
    poseidon_hash_3,
)
from ..common.domains import SHARD_MERKLE_POSEIDON_ROOT
from .tree import (
    ChunkMerkleProof,
    ShardMerkleTree,
    build_shard_tree,
    verify_chunk_proof,
)
from .global_tree import (
    GlobalShardMerkleTree,
    ShardRootProof,
    build_global_tree,
    verify_shard_root_proof,
)

from .service import GenerationChunkProof, ShardCommitment, ShardCommitmentService

__all__ = [
    "CHUNK_BYTES",
    "FIELD_MODULUS",
    "POSEIDON_ARITY",
    "SHARD_MERKLE_POSEIDON_ROOT",
    "hash_chunk",
    "hash_parent",
    "poseidon_hash_3",
    "ChunkMerkleProof",
    "ShardMerkleTree",
    "build_shard_tree",
    "verify_chunk_proof",
    "GlobalShardMerkleTree",
    "ShardRootProof",
    "build_global_tree",
    "verify_shard_root_proof",
    "GenerationChunkProof",
    "ShardCommitment",
    "ShardCommitmentService",
]
