"""Typed, JSON-serializable MMR structures."""

from __future__ import annotations
from typing import TypedDict


class MMRPeak(TypedDict):
    start_leaf: int
    height: int
    hash: str


class MMRNode(TypedDict):
    start_leaf: int
    height: int
    hash: str


class MMRState(TypedDict):
    schema_version: int
    leaf_count: int
    last_version: int
    peaks: list[MMRPeak]
    root: str
    nodes: list[MMRNode]
    manifests: list[dict[str, object]]
