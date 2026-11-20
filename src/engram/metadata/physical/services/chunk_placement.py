from ..models import (
    ChunkPlacement,
    GenerationManifestEntry,
    GenerationStatus,
)


class ChunkPlacementServiceError(Exception):
    """Base error for ChunkPlacement operations."""


class ChunkPlacementGenerationNotFoundError(ChunkPlacementServiceError):
    """The target Generation does not exist."""


class ChunkPlacementAlreadyRegisteredError(ChunkPlacementServiceError):
    """A Placement for the same Chunk already exists in the Generation."""


class ChunkPlacementPreconditionError(ChunkPlacementServiceError):
    """The Generation or supplied Placement is not eligible for registration."""


class ChunkPlacementNotFoundError(ChunkPlacementServiceError):
    """The requested ChunkPlacement does not exist."""


class ChunkPlacementIntegrityError(ChunkPlacementServiceError):
    """Persisted ChunkPlacement and Manifest metadata are inconsistent."""


class ChunkPlacementService:
    """Manage immutable Chunk-to-Generation record mappings only.

    The service does not access Logical Metadata, create or inspect payload
    records, manage shards or physical node locations, or delete placements.
    Writes are staged into the caller-owned batch and are never committed here.
    """

    def __init__(
        self,
        generation_repository,
        manifest_entry_repository,
        chunk_placement_repository,
    ) -> None:
        for name, repository in (
            ("generation_repository", generation_repository),
            ("manifest_entry_repository", manifest_entry_repository),
            ("chunk_placement_repository", chunk_placement_repository),
        ):
            if repository is None:
                raise TypeError(f"{name} is required")

        self._generations = generation_repository
        self._entries = manifest_entry_repository
        self._placements = chunk_placement_repository

    def register(
        self,
        placement: ChunkPlacement,
        manifest_entry: GenerationManifestEntry,
        batch,
    ) -> ChunkPlacement:
        """Stage one immutable Placement after matching its persisted Entry."""
        if not isinstance(placement, ChunkPlacement):
            raise TypeError("placement must be a ChunkPlacement")
        if not isinstance(manifest_entry, GenerationManifestEntry):
            raise TypeError("manifest_entry must be a GenerationManifestEntry")
        if batch is None:
            raise TypeError("batch is required")

        generation_id = placement.generation_id
        generation = self._generations.get(generation_id)
        if generation is None:
            raise ChunkPlacementGenerationNotFoundError(generation_id)
        if generation.status is not GenerationStatus.OPEN:
            raise ChunkPlacementPreconditionError(
                "ChunkPlacement can only be registered while Generation is OPEN"
            )

        self._require_placement_matches_entry(placement, manifest_entry)

        # The Entry may have been staged in the same caller-owned batch and
        # therefore is not visible through the repository yet. The orchestration
        # layer must stage this exact Entry in that batch (or have committed it
        # previously); post-commit validation verifies the persisted relation.

        if self._placements.get(placement.placement_id) is not None:
            raise ChunkPlacementAlreadyRegisteredError(
                f"ChunkPlacement {placement.placement_id!r} already exists"
            )

        existing_for_chunk = self._placements.list_for_chunk(
            placement.object_id, placement.chunk_id
        )
        if any(
            item.generation_id == generation_id for item in existing_for_chunk
        ):
            raise ChunkPlacementAlreadyRegisteredError(
                "A ChunkPlacement for this Object/Chunk already exists in the Generation"
            )

        # All service-level preconditions are checked before staging the write.
        # If the repository raises after staging, caller must discard the batch.
        self._placements.put(placement, batch)
        return placement

    def get(self, placement_id: str) -> ChunkPlacement | None:
        return self._placements.get(placement_id)

    def list_for_chunk(
        self, object_id: str, chunk_id: str
    ) -> tuple[ChunkPlacement, ...]:
        return self._placements.list_for_chunk(object_id, chunk_id)

    def validate(self, placement_id: str) -> bool:
        """Validate one stored Placement against its persisted Manifest Entry."""
        placement = self._placements.get(placement_id)
        if placement is None:
            raise ChunkPlacementNotFoundError(placement_id)

        generation = self._generations.get(placement.generation_id)
        if generation is None:
            raise ChunkPlacementIntegrityError(
                "ChunkPlacement references a missing Generation"
            )

        entries = self._entries.list_for_generation(placement.generation_id)
        matching_entries = tuple(
            entry for entry in entries if entry.record_id == placement.record_id
        )
        if len(matching_entries) != 1:
            raise ChunkPlacementIntegrityError(
                "ChunkPlacement must reference exactly one Manifest Entry"
            )

        try:
            self._require_placement_matches_entry(placement, matching_entries[0])
        except ChunkPlacementPreconditionError as exc:
            raise ChunkPlacementIntegrityError(
                "ChunkPlacement does not match its Manifest Entry"
            ) from exc

        same_chunk = self._placements.list_for_chunk(
            placement.object_id, placement.chunk_id
        )
        in_generation = tuple(
            item for item in same_chunk
            if item.generation_id == placement.generation_id
        )
        if len(in_generation) != 1 or in_generation[0].placement_id != placement_id:
            raise ChunkPlacementIntegrityError(
                "Object/Chunk has duplicate or inconsistent Placements in the Generation"
            )
        return True

    def validate_generation(self, generation_id: str) -> bool:
        """Validate that every Manifest Entry has exactly one matching Placement.

        The current repository has no generation-wide Placement index. This
        check therefore walks Manifest Entries and resolves each through the
        existing Object/Chunk secondary index. It cannot discover an orphan
        Placement whose Object/Chunk pair is absent from the Manifest.
        """
        generation = self._generations.get(generation_id)
        if generation is None:
            raise ChunkPlacementGenerationNotFoundError(generation_id)

        entries = self._entries.list_for_generation(generation_id)
        if not entries:
            raise ChunkPlacementIntegrityError(
                "Generation has no Manifest Entries to validate"
            )

        seen_chunks: set[tuple[str, str]] = set()
        for entry in entries:
            key = (entry.object_id, entry.chunk_id)
            if key in seen_chunks:
                raise ChunkPlacementIntegrityError(
                    "Manifest contains duplicate Object/Chunk pairs"
                )
            seen_chunks.add(key)

            placements = self._placements.list_for_chunk(*key)
            matching = tuple(
                item for item in placements if item.generation_id == generation_id
            )
            if len(matching) != 1:
                raise ChunkPlacementIntegrityError(
                    "Every Manifest Entry must have exactly one ChunkPlacement"
                )
            try:
                self._require_placement_matches_entry(matching[0], entry)
            except ChunkPlacementPreconditionError as exc:
                raise ChunkPlacementIntegrityError(
                    "ChunkPlacement does not match its Manifest Entry"
                ) from exc

        return True

    @staticmethod
    def _require_placement_matches_entry(
        placement: ChunkPlacement,
        entry: GenerationManifestEntry,
    ) -> None:
        if (
            placement.generation_id != entry.generation_id
            or placement.object_id != entry.object_id
            or placement.chunk_id != entry.chunk_id
            or placement.record_id != entry.record_id
        ):
            raise ChunkPlacementPreconditionError(
                "ChunkPlacement fields must match its GenerationManifestEntry"
            )
