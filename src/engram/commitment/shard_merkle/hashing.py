"""Poseidon hashing primitives for the two-level shard Merkle commitment."""

from __future__ import annotations

from ..common.domains import (
    SHARD_MERKLE_POSEIDON_LEAF,
    SHARD_MERKLE_POSEIDON_NODE,
)
from ..common.exceptions import CommitmentError, InvalidInputError
from ..common.validation import require_bytes, require_field_element, require_length

# BN254 scalar field modulus used by Circom's Poseidon implementation.
FIELD_MODULUS = (
    21888242871839275222246405745257275088548364400416034343698204186575808495617
)
CHUNK_BYTES = 31
POSEIDON_ARITY = 3


def poseidon_hash_3(a: int, b: int, c: int) -> int:
    """Return Circom-compatible Poseidon(a, b, c) over the BN254 scalar field."""
    a = require_field_element(a, FIELD_MODULUS, name="a")
    b = require_field_element(b, FIELD_MODULUS, name="b")
    c = require_field_element(c, FIELD_MODULUS, name="c")

    try:
        import fast_poseidon
    except ImportError as exc:
        raise CommitmentError(
            "fast_poseidon native backend is unavailable; build/install native/fast_poseidon"
        ) from exc

    try:
        result = fast_poseidon.hash_elements([str(a), str(b), str(c)])
    except Exception as exc:
        raise CommitmentError(
            "Poseidon backend failed to hash three field elements"
        ) from exc

    if type(result) is not str or not result.isdecimal():
        raise CommitmentError("Poseidon backend returned a non-decimal result")
    # Treat malformed/out-of-field backend output as a backend contract violation.
    try:
        return require_field_element(int(result), FIELD_MODULUS, name="Poseidon result")
    except (TypeError, ValueError) as exc:
        raise CommitmentError(
            "Poseidon backend returned an invalid field element"
        ) from exc


def hash_chunk(data: bytes) -> int:
    """Hash one non-empty chunk of at most 31 bytes, binding its exact length."""
    require_bytes(data, name="data")
    require_length(data, 1, CHUNK_BYTES, name="data")
    value = int.from_bytes(data, byteorder="big", signed=False)
    return poseidon_hash_3(SHARD_MERKLE_POSEIDON_LEAF, len(data), value)


def hash_parent(left: int, right: int) -> int:
    """Hash an ordered pair of child nodes."""
    left = require_field_element(left, FIELD_MODULUS, name="left")
    right = require_field_element(right, FIELD_MODULUS, name="right")
    return poseidon_hash_3(SHARD_MERKLE_POSEIDON_NODE, left, right)
