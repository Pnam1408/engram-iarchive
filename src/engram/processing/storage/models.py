"""Storage-related immutable data models."""

from __future__ import annotations

from dataclasses import dataclass
import re

from engram.processing.exceptions import InvalidArtifactError

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _require_non_empty_string(name: str, value: str) -> None:
    if not isinstance(value, str) or not value:
        raise InvalidArtifactError(f"{name} must be a non-empty string")


def _require_non_negative_int(name: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise InvalidArtifactError(f"{name} must be a non-negative integer")


def _require_positive_int(name: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise InvalidArtifactError(f"{name} must be a positive integer")


def _require_sha256(name: str, value: str) -> None:
    if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
        raise InvalidArtifactError(f"{name} must be a lowercase SHA-256 hex digest")


@dataclass(frozen=True, slots=True)
class StorageObject:
    """Metadata describing an object stored by a backend."""

    key: str
    size: int
    sha256: str

    def __post_init__(self) -> None:
        _require_non_empty_string("key", self.key)
        _require_non_negative_int("size", self.size)
        _require_sha256("sha256", self.sha256)


@dataclass(frozen=True, slots=True)
class StoredShardReference:
    """Reference to an encoded shard stored by a backend."""

    shard_index: int
    storage_key: str
    size: int
    sha256: str

    def __post_init__(self) -> None:
        _require_non_negative_int("shard_index", self.shard_index)
        _require_non_empty_string("storage_key", self.storage_key)
        _require_positive_int("size", self.size)
        _require_sha256("sha256", self.sha256)


@dataclass(frozen=True, slots=True)
class StoredShardSet:
    """References and coding metadata for a fully stored shard set."""

    generation_id: str
    original_size: int
    data_shards: int
    total_shards: int
    shards: tuple[StoredShardReference, ...]

    def __post_init__(self) -> None:
        _require_non_empty_string("generation_id", self.generation_id)
        _require_non_negative_int("original_size", self.original_size)
        _require_positive_int("data_shards", self.data_shards)
        _require_positive_int("total_shards", self.total_shards)

        if not 1 <= self.data_shards < self.total_shards <= 255:
            raise InvalidArtifactError("require 1 <= data_shards < total_shards <= 255")

        if not isinstance(self.shards, tuple):
            raise InvalidArtifactError("shards must be a tuple")

        if len(self.shards) != self.total_shards:
            raise InvalidArtifactError("shard count must equal total_shards")

        if any(not isinstance(shard, StoredShardReference) for shard in self.shards):
            raise InvalidArtifactError(
                "shards must contain StoredShardReference values"
            )

        expected_indexes = tuple(range(self.total_shards))
        actual_indexes = tuple(shard.shard_index for shard in self.shards)

        if actual_indexes != expected_indexes:
            raise InvalidArtifactError(
                "shard indexes must be ordered from zero without gaps"
            )

        storage_keys = tuple(shard.storage_key for shard in self.shards)

        if len(set(storage_keys)) != len(storage_keys):
            raise InvalidArtifactError("storage keys must be unique within a shard set")
