"""Typed proof structures for ENGRAM's MMR profile."""
from __future__ import annotations
from typing import TypedDict


class InclusionSibling(TypedDict):
    side: str  # "left" or "right"
    hash: str


class InclusionProof(TypedDict):
    leaf_index: int
    mmr_size: int
    peak_index: int
    leaf_hash: str
    siblings: list[InclusionSibling]
    peaks: list[dict[str, object]]


class AppendedSubtree(TypedDict):
    start_leaf: int
    height: int
    hash: str


class ConsistencyProof(TypedDict):
    old_size: int
    new_size: int
    old_peaks: list[dict[str, object]]
    appended_subtrees: list[AppendedSubtree]
