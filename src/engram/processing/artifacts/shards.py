"""Erasure-coded shard artifacts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ._validation import require_bytes, require_non_negative_int, require_positive_int, require_sha256
from engram.processing.exceptions import InvalidArtifactError


@dataclass(frozen=True, slots=True)
class EncodedShard:
    index: int
    data: bytes
    sha256: str

    def __post_init__(self) -> None:
        require_non_negative_int("index", self.index)
        require_bytes("data", self.data)
        if not self.data:
            raise InvalidArtifactError("shard data must not be empty")
        require_sha256("sha256", self.sha256)
        if hashlib.sha256(self.data).hexdigest() != self.sha256:
            raise InvalidArtifactError("sha256 does not match shard data")


@dataclass(frozen=True, slots=True)
class EncodedShardSet:
    shards: tuple[EncodedShard, ...]
    original_size: int
    data_shards: int
    total_shards: int

    def __post_init__(self) -> None:
        require_non_negative_int("original_size", self.original_size)
        require_positive_int("data_shards", self.data_shards)
        require_positive_int("total_shards", self.total_shards)
        if not self.data_shards < self.total_shards <= 255:
            raise InvalidArtifactError("require 1 <= data_shards < total_shards <= 255")
        if not isinstance(self.shards, tuple):
            raise InvalidArtifactError("shards must be a tuple")
        if len(self.shards) != self.total_shards:
            raise InvalidArtifactError("shard count must equal total_shards")
        if any(not isinstance(shard, EncodedShard) for shard in self.shards):
            raise InvalidArtifactError("shards must contain EncodedShard values")
        if tuple(shard.index for shard in self.shards) != tuple(range(self.total_shards)):
            raise InvalidArtifactError("shard indexes must be ordered from zero without gaps")
        if self.shards and len({len(shard.data) for shard in self.shards}) != 1:
            raise InvalidArtifactError("all shards must have equal size")
        if self.shards and self.original_size > len(self.shards[0].data) * self.data_shards:
            raise InvalidArtifactError("original_size exceeds shard data capacity")
