from __future__ import annotations

from ..models import ChunkRef, VersionDelta
from ..repositories import (
    ChunkRepository,
    ObjectChunkIndexRepository,
    ObjectChunkIndexSequenceConflictError,
)
from .delta_applier import DeltaApplier


class ObjectChunkIndexUpdateError(Exception):
    """Base error raised while deriving or staging an Object Chunk Index update."""


class MissingChunkCatalogRecordError(ObjectChunkIndexUpdateError):
    """An added ChunkRef references no Chunk in the Logical Chunk Catalog."""


class ObjectChunkIndexUpdater:
    """
    Applies a committed-candidate VersionDelta to the materialized Head index.

    The updater reads the current index, validates the expected index sequence,
    applies the Delta using the pure DeltaApplier, and stages only changed rows.
    The caller owns the MetadataBatch and commits it atomically with Version,
    VersionDelta, and Object Head records.
    """

    def __init__(
        self,
        index_repository: ObjectChunkIndexRepository,
        chunk_repository: ChunkRepository,
        *,
        delta_applier: DeltaApplier | None = None,
    ) -> None:
        if index_repository is None or chunk_repository is None:
            raise TypeError("index_repository and chunk_repository are required")
        self._index = index_repository
        self._chunks = chunk_repository
        self._applier = delta_applier or DeltaApplier()

    def stage_delta(
        self,
        *,
        object_id: str,
        delta: VersionDelta,
        expected_sequence: int,
        batch,
    ) -> tuple[ChunkRef, ...]:
        if not isinstance(delta, VersionDelta):
            raise TypeError("delta must be a VersionDelta")
        if delta.object_id != object_id:
            raise ObjectChunkIndexUpdateError(
                "Delta object_id does not match target Object"
            )
        if delta.sequence != expected_sequence + 1:
            raise ObjectChunkIndexUpdateError(
                "Delta sequence does not follow the expected Object Head"
            )

        current_sequence = self._index.get_sequence(object_id)
        if current_sequence != expected_sequence:
            raise ObjectChunkIndexSequenceConflictError(
                f"Expected index sequence {expected_sequence}, found {current_sequence}"
            )

        current = self._index.list_for_object(object_id)
        updated = self._applier.apply(current, delta)

        # A ChunkRef may reuse a ChunkID multiple times, but every newly added
        # reference must resolve to a Chunk Catalog record for this Object.
        for ref in delta.added_chunks:
            chunk = self._chunks.get(object_id, ref.chunk_id)
            if chunk is None:
                raise MissingChunkCatalogRecordError(
                    f"Chunk {ref.chunk_id!r} is absent from Object {object_id!r} catalog"
                )

        removed_ids = set(delta.removed_chunk_ref_ids)
        removed_refs = tuple(ref for ref in current if ref.chunk_ref_id in removed_ids)

        self._index.stage_changes(
            object_id=object_id,
            expected_sequence=expected_sequence,
            new_sequence=delta.sequence,
            removed_refs=removed_refs,
            added_refs=delta.added_chunks,
            batch=batch,
        )
        return updated
