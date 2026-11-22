"""High-level service for building and verifying two-level shard commitments."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..common.exceptions import InvalidInputError, InvalidTypeError
from .global_tree import (
    GlobalShardMerkleTree,
    ShardRootProof,
    build_global_tree,
    verify_shard_root_proof,
)
from .tree import (
    ChunkMerkleProof,
    ShardMerkleTree,
    build_shard_tree,
    verify_chunk_proof,
)


@dataclass(frozen=True, slots=True)
class GenerationChunkProof:
    """Combined proof that a chunk belongs to a shard committed by a global root."""

    shard_index: int
    chunk_proof: ChunkMerkleProof
    shard_proof: ShardRootProof


@dataclass(frozen=True, slots=True)
class ShardCommitment:
    """Immutable commitment result for an ordered set of shards."""

    shard_trees: tuple[ShardMerkleTree, ...]
    global_tree: GlobalShardMerkleTree

    @property
    def shard_count(self) -> int:
        return len(self.shard_trees)

    @property
    def shard_roots(self) -> tuple[int, ...]:
        return tuple(tree.root for tree in self.shard_trees)

    @property
    def global_root(self) -> int:
        return self.global_tree.root

    def proof(self, shard_index: int, chunk_index: int) -> GenerationChunkProof:
        """Create a two-level inclusion proof for the selected chunk."""
        shard_proof = self.global_tree.proof(shard_index)
        chunk_proof = self.shard_trees[shard_index].proof(chunk_index)
        return GenerationChunkProof(
            shard_index=shard_index,
            chunk_proof=chunk_proof,
            shard_proof=shard_proof,
        )


class ShardCommitmentService:
    """Build Generation-level shard commitments and verify combined chunk proofs.

    Shard position in the supplied sequence is the canonical shard index. This
    service does not persist results or perform metadata/workflow operations.
    """

    @staticmethod
    def build(shards: Sequence[bytes]) -> ShardCommitment:
        """Build inner shard trees and the global tree from ordered shard bytes."""
        if type(shards) not in (tuple, list):
            raise InvalidTypeError("shards must be a tuple or list of bytes")
        if not shards:
            raise InvalidInputError("shards must not be empty")
        shard_trees = tuple(build_shard_tree(shard) for shard in shards)
        global_tree = build_global_tree(tuple(tree.root for tree in shard_trees))
        return ShardCommitment(shard_trees=shard_trees, global_tree=global_tree)

    @staticmethod
    def verify(proof: GenerationChunkProof, global_root: int) -> bool:
        """Verify chunk inclusion through its shard root to the global root."""
        if not isinstance(proof, GenerationChunkProof):
            raise InvalidTypeError("proof must be GenerationChunkProof")
        if not isinstance(proof.chunk_proof, ChunkMerkleProof):
            raise InvalidTypeError("chunk_proof must be ChunkMerkleProof")
        if not isinstance(proof.shard_proof, ShardRootProof):
            raise InvalidTypeError("shard_proof must be ShardRootProof")
        if type(proof.shard_index) is not int:
            raise InvalidTypeError("shard_index must be an int (bool is not accepted)")
        if proof.shard_index < 0:
            raise InvalidInputError("shard_index must be non-negative")
        if proof.shard_proof.shard_index != proof.shard_index:
            return False
        if proof.chunk_proof.chunk_count <= 0:
            return False
        # Inner proof establishes the chunk under the shard root; outer proof
        # establishes that same root at the selected shard index globally.
        if not verify_chunk_proof(proof.chunk_proof, proof.shard_proof.shard_root):
            return False
        return verify_shard_root_proof(proof.shard_proof, global_root)
