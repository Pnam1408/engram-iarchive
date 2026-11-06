"""Custom append-only consistency proofs for ENGRAM's MMR profile."""
from __future__ import annotations
from collections.abc import Mapping
from typing import Any

from .hashing import HASH_SIZE, hash_node, hash_root
from .peaks import peak_layout
from .proof_types import ConsistencyProof


def _canonical_suffix_layout(old_size: int, new_size: int) -> list[tuple[int, int]]:
    if old_size < 0 or new_size < old_size:
        raise ValueError("Invalid MMR size range")
    result: list[tuple[int, int]] = []
    cursor = old_size
    while cursor < new_size:
        remaining = new_size - cursor
        max_height = remaining.bit_length() - 1
        if cursor == 0:
            height = max_height
        else:
            alignment_height = (cursor & -cursor).bit_length() - 1
            height = min(max_height, alignment_height)
        result.append((cursor, height))
        cursor += 1 << height
    return result


def generate_consistency_proof(accumulator: Any, old_size: int) -> ConsistencyProof:
    new_size = accumulator.leaf_count
    if not 0 <= old_size <= new_size:
        raise ValueError("old_size must be between zero and current leaf_count")
    old_layout = peak_layout(old_size)
    suffix_layout = _canonical_suffix_layout(old_size, new_size)
    return {
        "old_size": old_size,
        "new_size": new_size,
        "old_peaks": [
            {"start_leaf": start, "height": height,
             "hash": accumulator.get_node(start, height).hex()}
            for start, height in old_layout
        ],
        "appended_subtrees": [
            {"start_leaf": start, "height": height,
             "hash": accumulator.get_node(start, height).hex()}
            for start, height in suffix_layout
        ],
    }


def verify_consistency_proof(
    proof: Mapping[str, Any], old_root: bytes | str, new_root: bytes | str
) -> bool:
    """Verify that new_root extends old_root using only the proof payload."""
    try:
        old_size = proof["old_size"]
        new_size = proof["new_size"]
        if not isinstance(old_size, int) or not isinstance(new_size, int):
            return False
        if old_size < 0 or new_size < old_size:
            return False
        old_layout = peak_layout(old_size)
        if len(proof["old_peaks"]) != len(old_layout):
            return False
        peaks: list[tuple[int, int, bytes]] = []
        old_hashes: list[tuple[int, bytes]] = []
        for item, (start, height) in zip(proof["old_peaks"], old_layout, strict=True):
            if item["start_leaf"] != start or item["height"] != height:
                return False
            digest = bytes.fromhex(item["hash"])
            if len(digest) != HASH_SIZE:
                return False
            peaks.append((start, height, digest))
            old_hashes.append((height, digest))
        old_digest = hash_root(old_size, old_hashes)
        expected_old = bytes.fromhex(old_root) if isinstance(old_root, str) else old_root
        if len(expected_old) != HASH_SIZE or old_digest != expected_old:
            return False

        expected_suffix = _canonical_suffix_layout(old_size, new_size)
        supplied = proof["appended_subtrees"]
        if len(supplied) != len(expected_suffix):
            return False
        for item, (start, height) in zip(supplied, expected_suffix, strict=True):
            if item["start_leaf"] != start or item["height"] != height:
                return False
            digest = bytes.fromhex(item["hash"])
            if len(digest) != HASH_SIZE:
                return False
            current_start = start
            current_height = height
            while peaks and peaks[-1][1] == current_height:
                left_start, _, left_hash = peaks.pop()
                if left_start + (1 << current_height) != current_start:
                    return False
                digest = hash_node(left_hash, digest)
                current_start = left_start
                current_height += 1
            peaks.append((current_start, current_height, digest))

        expected_new_layout = peak_layout(new_size)
        if [(start, height) for start, height, _ in peaks] != expected_new_layout:
            return False
        new_digest = hash_root(new_size, [(height, digest) for _, height, digest in peaks])
        expected_new = bytes.fromhex(new_root) if isinstance(new_root, str) else new_root
        return len(expected_new) == HASH_SIZE and new_digest == expected_new
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
