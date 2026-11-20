from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Callable

from ..models import Generation, GenerationStatus
from ._availability import ShardAvailabilitySnapshot

if TYPE_CHECKING:
    from ...database.batch import MetadataBatch
    from ..repositories import (
        GenerationManifestEntryRepository,
        GenerationManifestRepository,
        GenerationRepository,
        GenerationShardRepository,
    )


class GenerationLifecycleError(Exception):
    """Base error for Generation lifecycle operations."""


class GenerationNotFoundError(GenerationLifecycleError):
    pass


class GenerationAlreadyExistsError(GenerationLifecycleError):
    pass


class InvalidGenerationTransitionError(GenerationLifecycleError):
    pass


class GenerationPreconditionError(GenerationLifecycleError):
    pass


class GenerationConcurrencyError(GenerationLifecycleError):
    pass


class GenerationLifecycleService:
    """Owns Generation state transitions; never commits caller-owned batches."""

    _TRANSITIONS = {
        GenerationStatus.OPEN: {GenerationStatus.SEALED, GenerationStatus.FAILED},
        GenerationStatus.SEALED: {GenerationStatus.ENCODING, GenerationStatus.FAILED},
        GenerationStatus.ENCODING: {GenerationStatus.ENCODED, GenerationStatus.FAILED},
        GenerationStatus.ENCODED: {GenerationStatus.STORING, GenerationStatus.FAILED},
        GenerationStatus.STORING: {
            GenerationStatus.AVAILABLE,
            GenerationStatus.DEGRADED,
            GenerationStatus.FAILED,
            GenerationStatus.LOST,
        },
        GenerationStatus.AVAILABLE: {GenerationStatus.DEGRADED, GenerationStatus.LOST},
        GenerationStatus.DEGRADED: {GenerationStatus.AVAILABLE, GenerationStatus.LOST},
        GenerationStatus.FAILED: set(),
        GenerationStatus.LOST: set(),
    }

    def __init__(
        self,
        generation_repository: GenerationRepository,
        manifest_repository: GenerationManifestRepository,
        manifest_entry_repository: GenerationManifestEntryRepository,
        shard_repository: GenerationShardRepository,
        *,
        clock: Callable[[], int] | None = None,
    ):
        for name, value in (
            ("generation_repository", generation_repository),
            ("manifest_repository", manifest_repository),
            ("manifest_entry_repository", manifest_entry_repository),
            ("shard_repository", shard_repository),
        ):
            if value is None:
                raise TypeError(f"{name} is required")
        self._generations = generation_repository
        self._manifests = manifest_repository
        self._manifest_entries = manifest_entry_repository
        self._shards = shard_repository
        self._clock = clock or (lambda: __import__("time").time_ns() // 1_000_000)

    def get(self, generation_id: str) -> Generation | None:
        return self._generations.get(generation_id)

    def require(self, generation_id: str) -> Generation:
        generation = self.get(generation_id)
        if generation is None:
            raise GenerationNotFoundError(generation_id)
        return generation

    def get_status(self, generation_id: str) -> GenerationStatus | None:
        generation = self.get(generation_id)
        return None if generation is None else generation.status

    def create(self, generation: Generation, *, batch: MetadataBatch) -> None:
        if not isinstance(generation, Generation):
            raise TypeError("generation must be a Generation")
        if batch is None:
            raise TypeError("batch is required")
        if generation.status is not GenerationStatus.OPEN:
            raise GenerationPreconditionError("A new Generation must start OPEN")
        if any(
            value is not None
            for value in (
                generation.sealed_at,
                generation.encoded_at,
                generation.completed_at,
            )
        ):
            raise GenerationPreconditionError(
                "A new OPEN Generation cannot have lifecycle timestamps"
            )
        if self._generations.get(generation.generation_id) is not None:
            raise GenerationAlreadyExistsError(generation.generation_id)
        self._generations.put(generation, batch)

    def seal(
        self, generation_id: str, expected_updated_at: int, *, batch: MetadataBatch
    ) -> Generation:
        current = self._load_for_transition(
            generation_id, expected_updated_at, GenerationStatus.SEALED, batch
        )
        manifest = self._manifests.get(generation_id)
        if manifest is None:
            raise GenerationPreconditionError("Generation Manifest is missing")
        entries = self._manifest_entries.list_for_generation(generation_id)
        try:
            manifest.validate_entries(entries)
        except (TypeError, ValueError) as exc:
            raise GenerationPreconditionError(
                "Generation Manifest entries are incomplete or invalid"
            ) from exc
        now = self._now_after(current)
        return self._stage(
            replace(
                current, status=GenerationStatus.SEALED, sealed_at=now, updated_at=now
            ),
            batch,
        )

    def start_encoding(
        self, generation_id: str, expected_updated_at: int, *, batch: MetadataBatch
    ) -> Generation:
        current = self._load_for_transition(
            generation_id, expected_updated_at, GenerationStatus.ENCODING, batch
        )
        now = self._now_after(current)
        return self._stage(
            replace(current, status=GenerationStatus.ENCODING, updated_at=now), batch
        )

    def mark_encoded(
        self, generation_id: str, expected_updated_at: int, *, batch: MetadataBatch
    ) -> Generation:
        current = self._load_for_transition(
            generation_id, expected_updated_at, GenerationStatus.ENCODED, batch
        )
        shards = self._shards.list_for_generation(generation_id)
        expected = set(range(current.total_shards))
        actual = {shard.shard_index for shard in shards}
        if actual != expected or any(
            shard.generation_id != generation_id for shard in shards
        ):
            raise GenerationPreconditionError(
                "Generation must have exactly one registered shard for every index"
            )
        now = self._now_after(current)
        return self._stage(
            replace(
                current, status=GenerationStatus.ENCODED, encoded_at=now, updated_at=now
            ),
            batch,
        )

    def start_storing(
        self, generation_id: str, expected_updated_at: int, *, batch: MetadataBatch
    ) -> Generation:
        current = self._load_for_transition(
            generation_id, expected_updated_at, GenerationStatus.STORING, batch
        )
        self._require_complete_shards(current)
        now = self._now_after(current)
        return self._stage(
            replace(current, status=GenerationStatus.STORING, updated_at=now), batch
        )

    def mark_available(
        self,
        generation_id: str,
        expected_updated_at: int,
        availability: ShardAvailabilitySnapshot,
        *,
        batch: MetadataBatch,
    ) -> Generation:
        return self._mark_availability(
            generation_id,
            expected_updated_at,
            availability,
            GenerationStatus.AVAILABLE,
            batch,
        )

    def mark_degraded(
        self,
        generation_id: str,
        expected_updated_at: int,
        availability: ShardAvailabilitySnapshot,
        *,
        batch: MetadataBatch,
    ) -> Generation:
        return self._mark_availability(
            generation_id,
            expected_updated_at,
            availability,
            GenerationStatus.DEGRADED,
            batch,
        )

    def mark_lost(
        self,
        generation_id: str,
        expected_updated_at: int,
        availability: ShardAvailabilitySnapshot,
        *,
        batch: MetadataBatch,
    ) -> Generation:
        return self._mark_availability(
            generation_id,
            expected_updated_at,
            availability,
            GenerationStatus.LOST,
            batch,
        )

    def mark_failed(
        self, generation_id: str, expected_updated_at: int, *, batch: MetadataBatch
    ) -> Generation:
        current = self.require(generation_id)
        self._check_version(current, expected_updated_at)
        self._check_transition(current, GenerationStatus.FAILED)
        now = self._now_after(current)
        return self._stage(
            replace(
                current,
                status=GenerationStatus.FAILED,
                completed_at=current.completed_at or now,
                updated_at=now,
            ),
            batch,
        )

    def _mark_availability(
        self, generation_id, expected_updated_at, availability, target, batch
    ):
        current = self.require(generation_id)
        self._check_version(current, expected_updated_at)
        self._check_transition(current, target)
        self._require_complete_shards(current)
        indices = self._snapshot_indices(current, availability)
        count = len(indices)
        if count == current.total_shards:
            actual = GenerationStatus.AVAILABLE
        elif count >= current.data_shards:
            actual = GenerationStatus.DEGRADED
        else:
            actual = GenerationStatus.LOST
        if actual is not target:
            raise GenerationPreconditionError(
                f"Availability snapshot classifies as {actual.value}, not {target.value}"
            )
        now = self._now_after(current)
        return self._stage(
            replace(
                current,
                status=target,
                completed_at=current.completed_at or now,
                updated_at=now,
            ),
            batch,
        )

    def _snapshot_indices(self, generation, snapshot):
        if not isinstance(snapshot, ShardAvailabilitySnapshot):
            raise TypeError("availability must be a ShardAvailabilitySnapshot")
        if snapshot.generation_id != generation.generation_id:
            raise GenerationPreconditionError(
                "Availability snapshot belongs to another Generation"
            )
        indices = snapshot.verified_shard_indices
        if not isinstance(indices, frozenset):
            raise TypeError("verified_shard_indices must be a frozenset")
        if any(
            isinstance(i, bool)
            or not isinstance(i, int)
            or i < 0
            or i >= generation.total_shards
            for i in indices
        ):
            raise GenerationPreconditionError(
                "Availability snapshot contains an invalid shard index"
            )
        return indices

    def _load_for_transition(self, generation_id, expected_updated_at, target, batch):
        if batch is None:
            raise TypeError("batch is required")
        current = self.require(generation_id)
        self._check_version(current, expected_updated_at)
        self._check_transition(current, target)
        return current

    @staticmethod
    def _check_version(current, expected_updated_at):
        if isinstance(expected_updated_at, bool) or not isinstance(
            expected_updated_at, int
        ):
            raise TypeError("expected_updated_at must be an integer")
        if current.updated_at != expected_updated_at:
            raise GenerationConcurrencyError("Generation has changed since it was read")

    def _check_transition(self, current, target):
        if target not in self._TRANSITIONS[current.status]:
            raise InvalidGenerationTransitionError(
                f"Transition {current.status.value} -> {target.value} is not allowed"
            )

    def _require_complete_shards(self, generation):
        shards = self._shards.list_for_generation(generation.generation_id)
        if {shard.shard_index for shard in shards} != set(
            range(generation.total_shards)
        ):
            raise GenerationPreconditionError(
                "Generation does not have a complete shard set"
            )

    def _now_after(self, current):
        now = self._clock()
        if (
            isinstance(now, bool)
            or not isinstance(now, int)
            or now < current.updated_at
        ):
            raise GenerationLifecycleError(
                "Clock must return an integer timestamp >= updated_at"
            )
        return now

    def _stage(self, generation, batch):
        if batch is None:
            raise TypeError("batch is required")
        self._generations.put(generation, batch)
        return generation
