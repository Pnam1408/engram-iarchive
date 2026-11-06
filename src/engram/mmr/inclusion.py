"""MMR inclusion proof generation and independent verification."""
from __future__ import annotations
from collections.abc import Mapping
from typing import Any

from .hashing import HASH_SIZE, hash_node, hash_root
from .peaks import peak_layout
from .proof_types import InclusionProof


def generate_inclusion_proof(accumulator: Any, leaf_index: int) -> InclusionProof:
    if not 0 <= leaf_index < accumulator.leaf_count:
        raise IndexError("leaf_index is outside the MMR")
    layout = peak_layout(accumulator.leaf_count)
    peak_index = next(
        i for i, (start, height) in enumerate(layout)
        if start <= leaf_index < start + (1 << height)
    )
    mountain_start, mountain_height = layout[peak_index]
    local_index = leaf_index - mountain_start
    siblings: list[dict[str, str]] = []
    for height in range(mountain_height):
        group_index = local_index >> height
        sibling_group = group_index ^ 1
        sibling_start = mountain_start + sibling_group * (1 << height)
        side = "left" if group_index & 1 else "right"
        siblings.append({
            "side": side,
            "hash": accumulator.get_node(sibling_start, height).hex(),
        })
    return {
        "leaf_index": leaf_index,
        "mmr_size": accumulator.leaf_count,
        "peak_index": peak_index,
        "leaf_hash": accumulator.get_node(leaf_index, 0).hex(),
        "siblings": siblings,
        "peaks": [
            {"start_leaf": start, "height": height,
             "hash": accumulator.get_node(start, height).hex()}
            for start, height in layout
        ],
    }


def verify_inclusion_proof(proof: Mapping[str, Any], expected_root: bytes | str) -> bool:
    """Verify without access to the accumulator or persisted node database."""
    try:
        leaf_index = proof["leaf_index"]
        mmr_size = proof["mmr_size"]
        peak_index = proof["peak_index"]
        if not isinstance(leaf_index, int) or not isinstance(mmr_size, int):
            return False
        if not 0 <= leaf_index < mmr_size:
            return False
        layout = peak_layout(mmr_size)
        if not isinstance(peak_index, int) or not 0 <= peak_index < len(layout):
            return False
        if len(proof["peaks"]) != len(layout):
            return False
        expected_peaks: list[tuple[int, bytes]] = []
        selected_hash: bytes | None = None
        selected_start, selected_height = layout[peak_index]
        if not selected_start <= leaf_index < selected_start + (1 << selected_height):
            return False
        siblings = proof["siblings"]
        if len(siblings) != selected_height:
            return False
        current = bytes.fromhex(proof["leaf_hash"])
        if len(current) != HASH_SIZE:
            return False
        local_index = leaf_index - selected_start
        for height, sibling in enumerate(siblings):
            sibling_hash = bytes.fromhex(sibling["hash"])
            if len(sibling_hash) != HASH_SIZE:
                return False
            expected_side = "left" if ((local_index >> height) & 1) else "right"
            if sibling["side"] != expected_side:
                return False
            current = hash_node(sibling_hash, current) if expected_side == "left" else hash_node(current, sibling_hash)
        selected_hash = current
        for i, (start, height) in enumerate(layout):
            item = proof["peaks"][i]
            if item["start_leaf"] != start or item["height"] != height:
                return False
            digest = bytes.fromhex(item["hash"])
            if len(digest) != HASH_SIZE:
                return False
            if i == peak_index and digest != selected_hash:
                return False
            expected_peaks.append((height, digest))
        calculated = hash_root(mmr_size, expected_peaks)
        root = bytes.fromhex(expected_root) if isinstance(expected_root, str) else expected_root
        return len(root) == HASH_SIZE and calculated == root
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
