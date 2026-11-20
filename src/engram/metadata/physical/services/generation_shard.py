from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..models import GenerationShard, GenerationStatus

if TYPE_CHECKING:
    from ...database.batch import MetadataBatch
    from ..repositories import GenerationRepository, GenerationShardRepository


class GenerationShardServiceError(Exception):
    """Base error for GenerationShard operations."""


class GenerationShardGenerationNotFoundError(GenerationShardServiceError):
    """The target Generation does not exist."""


class GenerationShardAlreadyRegisteredError(GenerationShardServiceError):
    """Shard metadata already exists for the target Generation."""


class GenerationShardPreconditionError(GenerationShardServiceError):
    """The Generation or supplied shard set is not eligible for registration."""


class GenerationShardNotFoundError(GenerationShardServiceError):
    """The requested GenerationShard does not exist."""


class GenerationShardIntegrityError(GenerationShardServiceError):
    """Persisted GenerationShard metadata is incomplete or inconsistent."""


@dataclass(frozen=True, slots=True)
class GenerationShardSet:
    """A complete, index-ordered set of shard metadata for one Generation."""

    generation_id: str
    shards: tuple[GenerationShard, ...]


class GenerationShardService:
    """Manage immutable GenerationShard metadata only.

    The service does not perform erasure encoding, calculate shard hashes,
    inspect shard bytes, create placements, or access Processing / Logical
    Metadata. Writes are staged into the caller-owned batch and never committed
    by this service.
    """

    def __init__(
        self,
        generation_repository: GenerationRepository,
        shard_repository: GenerationShardRepository,
    ) -> None:
        if generation_repository is None:
            raise TypeError("generation_repository is required")
        if shard_repository is None:
            raise TypeError("shard_repository is required")
        self._generations = generation_repository
        self._shards = shard_repository

    def register_shards(
        self,
        generation_id: str,
        shards: tuple[GenerationShard, ...],
        *,
        batch: MetadataBatch,
    ) -> GenerationShardSet:
        """Stage one complete shard set for a Generation in ENCODING state."""
        if not isinstance(generation_id, str) or not generation_id.strip():
            raise ValueError("generation_id must be a non-empty string")
        if not isinstance(shards, tuple) or any(
            not isinstance(shard, GenerationShard) for shard in shards
        ):
            raise TypeError("shards must be a tuple of GenerationShard")
        if batch is None:
            raise TypeError("batch is required")

        generation = self._generations.get(generation_id)
        if generation is None:
            raise GenerationShardGenerationNotFoundError(generation_id)
        if generation.status is not GenerationStatus.ENCODING:
            raise GenerationShardPreconditionError(
                "Shard metadata can only be registered while Generation is ENCODING"
            )

        expected_indices = set(range(generation.total_shards))
        actual_indices = [shard.shard_index for shard in shards]
        if any(shard.generation_id != generation_id for shard in shards):
            raise GenerationShardPreconditionError(
                "Every shard must belong to the target Generation"
            )
        if len(actual_indices) != len(set(actual_indices)):
            raise GenerationShardPreconditionError(
                "Shard indices must be unique within a Generation"
            )
        if set(actual_indices) != expected_indices:
            raise GenerationShardPreconditionError(
                "Shard set must contain exactly indices 0..total_shards-1"
            )

        # Normalize the returned set and staged write order by shard index.
        ordered_shards = tuple(sorted(shards, key=lambda shard: shard.shard_index))
        existing = self._shards.list_for_generation(generation_id)
        if existing:
            raise GenerationShardAlreadyRegisteredError(
                f"Shard metadata for Generation {generation_id!r} already exists"
            )

        # Validate all inputs and existing state before the first write. If a
        # repository write fails after staging begins, caller must discard batch.
        for shard in ordered_shards:
            self._shards.put(shard, batch)
        return GenerationShardSet(generation_id, ordered_shards)

    def get_shard(self, generation_id: str, shard_index: int) -> GenerationShard | None:
        return self._shards.get(generation_id, shard_index)

    def list_shards(self, generation_id: str) -> tuple[GenerationShard, ...]:
        return self._shards.list_for_generation(generation_id)

    def get_shard_set(self, generation_id: str) -> GenerationShardSet | None:
        """Return a validated complete set, or None if no shards are registered."""
        generation = self._generations.get(generation_id)
        if generation is None:
            raise GenerationShardGenerationNotFoundError(generation_id)
        shards = self._shards.list_for_generation(generation_id)
        if not shards:
            return None
        self._validate_shard_set(generation_id, generation.total_shards, shards)
        return GenerationShardSet(generation_id, shards)

    def validate(self, generation_id: str) -> bool:
        """Validate persisted shard metadata against Generation.total_shards."""
        generation = self._generations.get(generation_id)
        if generation is None:
            raise GenerationShardGenerationNotFoundError(generation_id)
        shards = self._shards.list_for_generation(generation_id)
        if not shards:
            raise GenerationShardNotFoundError(
                f"No shard metadata registered for Generation {generation_id!r}"
            )
        self._validate_shard_set(generation_id, generation.total_shards, shards)
        return True

    @staticmethod
    def _validate_shard_set(
        generation_id: str,
        total_shards: int,
        shards: tuple[GenerationShard, ...],
    ) -> None:
        expected = set(range(total_shards))
        indices = [shard.shard_index for shard in shards]
        if any(shard.generation_id != generation_id for shard in shards):
            raise GenerationShardIntegrityError(
                "Stored GenerationShard belongs to another Generation"
            )
        if len(indices) != len(set(indices)) or set(indices) != expected:
            raise GenerationShardIntegrityError(
                "Stored GenerationShard set is incomplete or has duplicate indices"
            )
