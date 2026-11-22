"""Global Merkle tree over ordered shard roots for one generation."""

from __future__ import annotations


from dataclasses import dataclass
from typing import Sequence


from ..common.domains import (
    SHARD_MERKLE_POSEIDON_GLOBAL_LEAF,
    SHARD_MERKLE_POSEIDON_GLOBAL_ROOT,
)
from ..common.exceptions import (
    InvalidInputError,
    InvalidTypeError,
    ValueOutOfRangeError,
)
from ..common.validation import require_field_element
from .hashing import FIELD_MODULUS, hash_parent, poseidon_hash_3


@dataclass(frozen=True, slots=True)
class ShardRootProof:
    """Inclusion proof for one shard root in the generation's global tree."""

    shard_index: int
    shard_count: int
    shard_root: int
    siblings: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class GlobalShardMerkleTree:
    """Immutable global tree; shard roots are committed in shard-index order."""

    shard_roots: tuple[int, ...]
    leaf_hashes: tuple[int, ...]
    levels: tuple[tuple[int, ...], ...]
    root: int

    @property
    def shard_count(self) -> int:
        return len(self.shard_roots)

    def proof(self, shard_index: int) -> ShardRootProof:
        if type(shard_index) is not int:
            raise InvalidTypeError("shard_index must be an int (bool is not accepted)")
        if shard_index < 0 or shard_index >= self.shard_count:
            raise ValueOutOfRangeError("shard_index is outside the global tree")
        siblings: list[int] = []
        index = shard_index
        for level in self.levels[:-1]:
            sibling_index = index ^ 1
            if sibling_index >= len(level):
                sibling_index = index
            siblings.append(level[sibling_index])
            index //= 2
        return ShardRootProof(
            shard_index=shard_index,
            shard_count=self.shard_count,
            shard_root=self.shard_roots[shard_index],
            siblings=tuple(siblings),
        )


def _validate_shard_roots(shard_roots: Sequence[int]) -> tuple[int, ...]:
    if type(shard_roots) not in (tuple, list):
        raise InvalidTypeError("shard_roots must be a tuple or list of field elements")
    if not shard_roots:
        raise InvalidInputError("shard_roots must not be empty")
    return tuple(
        require_field_element(root, FIELD_MODULUS, name=f"shard_roots[{index}]")
        for index, root in enumerate(shard_roots)
    )


def _hash_global_leaf(shard_index: int, shard_root: int) -> int:
    return poseidon_hash_3(
        SHARD_MERKLE_POSEIDON_GLOBAL_LEAF,
        shard_index,
        shard_root,
    )


def build_global_tree(shard_roots: Sequence[int]) -> GlobalShardMerkleTree:
    """Build a global Merkle tree from shard roots in canonical index order.

    Each leaf binds both its position (shard index) and the shard root. Odd-width
    levels use duplicate-last. The final root binds shard_count and tree_root.
    """
    roots = _validate_shard_roots(shard_roots)
    leaves = tuple(_hash_global_leaf(index, root) for index, root in enumerate(roots))
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
    root = poseidon_hash_3(
        SHARD_MERKLE_POSEIDON_GLOBAL_ROOT,
        len(roots),
        levels[-1][0],
    )
    return GlobalShardMerkleTree(
        shard_roots=roots,
        leaf_hashes=leaves,
        levels=tuple(levels),
        root=root,
    )


def verify_shard_root_proof(proof: ShardRootProof, global_root: int) -> bool:
    """Verify a shard-root inclusion proof against a generation global root."""
    if not isinstance(proof, ShardRootProof):
        raise InvalidTypeError("proof must be ShardRootProof")
    global_root = require_field_element(global_root, FIELD_MODULUS, name="global_root")
    if type(proof.shard_index) is not int or type(proof.shard_count) is not int:
        raise InvalidTypeError("proof indices must be ints")
    if (
        proof.shard_count <= 0
        or proof.shard_index < 0
        or proof.shard_index >= proof.shard_count
    ):
        raise ValueOutOfRangeError("proof indices are invalid")
    shard_root = require_field_element(
        proof.shard_root, FIELD_MODULUS, name="proof.shard_root"
    )
    expected_depth = 0
    width = proof.shard_count
    while width > 1:
        expected_depth += 1
        width = (width + 1) // 2
    if type(proof.siblings) is not tuple or len(proof.siblings) != expected_depth:
        raise InvalidInputError("proof sibling count does not match shard_count")

    current = _hash_global_leaf(proof.shard_index, shard_root)
    index = proof.shard_index
    width = proof.shard_count
    for sibling_value in proof.siblings:
        sibling = require_field_element(
            sibling_value, FIELD_MODULUS, name="proof sibling"
        )
        if index % 2 == 0:
            if index + 1 >= width and sibling != current:
                return False
            current = hash_parent(current, sibling)
        else:
            current = hash_parent(sibling, current)
        index //= 2
        width = (width + 1) // 2
    current = poseidon_hash_3(
        SHARD_MERKLE_POSEIDON_GLOBAL_ROOT, proof.shard_count, current
    )
    return current == global_root
