"""Merkle Mountain Range primitives and proofs for ENGRAM version history."""

from .accumulator import MMRAccumulator
from .consistency import generate_consistency_proof, verify_consistency_proof
from .hashing import (
    canonical_manifest_bytes,
    hash_leaf,
    hash_node,
    hash_root,
    manifest_hash,
)
from .inclusion import generate_inclusion_proof, verify_inclusion_proof
from .types import MMRNode, MMRPeak, MMRState

__all__ = [
    "MMRAccumulator",
    "MMRNode",
    "MMRPeak",
    "MMRState",
    "canonical_manifest_bytes",
    "hash_leaf",
    "hash_node",
    "hash_root",
    "manifest_hash",
    "generate_inclusion_proof",
    "verify_inclusion_proof",
    "generate_consistency_proof",
    "verify_consistency_proof",
]
