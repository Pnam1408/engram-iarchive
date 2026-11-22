"""Inner Merkle tree construction and chunk inclusion proofs for a single shard."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from ..common.exceptions import (
    InvalidInputError,
    InvalidTypeError,
    ValueOutOfRangeError,
)
from ..common.validation import require_bytes, require_field_element
from ..common.domains import SHARD_MERKLE_POSEIDON_ROOT
from .hashing import (
    CHUNK_BYTES,
    FIELD_MODULUS,
    hash_chunk,
    hash_parent,
    poseidon_hash_3,
)


@dataclass(frozen=True, slots=True)
class ChunkMerkleProof:
    """Inclusion proof for one 31-byte chunk in a shard's inner Merkle tree."""

    chunk_index: int
    chunk_count: int
    chunk: bytes
    siblings: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ShardMerkleTree:
    """Immutable inner Merkle tree for the chunks of one shard."""

    shard_size: int
    chunks: tuple[bytes, ...]
    leaf_hashes: tuple[int, ...]
    levels: tuple[tuple[int, ...], ...]
    root: int

    @property
    def chunk_count(self) -> int:
        return len(self.chunks)

    def proof(self, chunk_index: int) -> ChunkMerkleProof:
        if type(chunk_index) is not int:
            raise InvalidTypeError("chunk_index must be an int (bool is not accepted)")
        if chunk_index < 0 or chunk_index >= self.chunk_count:
            raise ValueOutOfRangeError("chunk_index is outside the shard")
        siblings: list[int] = []
        index = chunk_index
        for level in self.levels[:-1]:
            sibling_index = index ^ 1
            # The final unpaired node is duplicated when constructing the level.
            if sibling_index >= len(level):
                sibling_index = index
            siblings.append(level[sibling_index])
            index //= 2
        return ChunkMerkleProof(
            chunk_index=chunk_index,
            chunk_count=self.chunk_count,
            chunk=self.chunks[chunk_index],
            siblings=tuple(siblings),
        )


def _split_chunks(shard_bytes: bytes) -> tuple[bytes, ...]:
    require_bytes(shard_bytes, name="shard_bytes")
    if not shard_bytes:
        raise InvalidInputError("shard_bytes must not be empty")
    return tuple(
        shard_bytes[offset : offset + CHUNK_BYTES]
        for offset in range(0, len(shard_bytes), CHUNK_BYTES)
    )


def build_shard_tree(shard_bytes: bytes) -> ShardMerkleTree:
    """Build a chunk-level Merkle tree; odd-width levels duplicate their last node."""
    chunks = _split_chunks(shard_bytes)
    leaves = tuple(hash_chunk(chunk) for chunk in chunks)
    levels: list[tuple[int, ...]] = [leaves]
    current = leaves
    while len(current) > 1:
        next_level: list[int] = []
        for offset in range(0, len(current), 2):
            left = current[offset]
            right = current[offset + 1] if offset + 1 < len(current) else left
            next_level.append(hash_parent(left, right))
        current = tuple(next_level)
        levels.append(current)
    # Bind the logical tree width to the root. Without this wrapper, duplicate-last
    # trees can have shape ambiguities (e.g. 3 leaves vs. 4 with a repeated tail).
    root = poseidon_hash_3(SHARD_MERKLE_POSEIDON_ROOT, len(chunks), levels[-1][0])
    return ShardMerkleTree(
        shard_size=len(shard_bytes),
        chunks=chunks,
        leaf_hashes=leaves,
        levels=tuple(levels),
        root=root,
    )


def verify_chunk_proof(proof: ChunkMerkleProof, root: int) -> bool:
    """Verify an inclusion proof for a chunk against a shard Merkle root."""
    if not isinstance(proof, ChunkMerkleProof):
        raise InvalidTypeError("proof must be ChunkMerkleProof")
    root = require_field_element(root, FIELD_MODULUS, name="root")
    if type(proof.chunk_index) is not int or type(proof.chunk_count) is not int:
        raise InvalidTypeError("proof indices must be ints")
    if (
        proof.chunk_count <= 0
        or proof.chunk_index < 0
        or proof.chunk_index >= proof.chunk_count
    ):
        raise ValueOutOfRangeError("proof indices are invalid")
    require_bytes(proof.chunk, name="proof.chunk")
    if not 1 <= len(proof.chunk) <= CHUNK_BYTES:
        raise ValueOutOfRangeError("proof.chunk length must be 1..31 bytes")

    expected_depth = 0
    width = proof.chunk_count
    while width > 1:
        expected_depth += 1
        width = (width + 1) // 2
    if len(proof.siblings) != expected_depth:
        raise InvalidInputError("proof sibling count does not match chunk_count")
    current = hash_chunk(proof.chunk)
    index = proof.chunk_index
    width = proof.chunk_count
    for sibling in proof.siblings:
        sibling = require_field_element(sibling, FIELD_MODULUS, name="proof sibling")
        if index % 2 == 0:
            # At an odd-width level, the last node is its own sibling.
            if index + 1 >= width and sibling != current:
                return False
            current = hash_parent(current, sibling)
        else:
            current = hash_parent(sibling, current)
        index //= 2
        width = (width + 1) // 2
    current = poseidon_hash_3(SHARD_MERKLE_POSEIDON_ROOT, proof.chunk_count, current)
    return current == root
