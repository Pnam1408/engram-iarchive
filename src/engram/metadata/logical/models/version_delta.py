from dataclasses import dataclass

from ._validation import (
    validate_timestamp,
    validate_uuid,
    validate_non_empty_string,
    validate_positive_integer,
)
from .chunk_ref import ChunkRef


@dataclass(frozen=True, slots=True)
class VersionDelta:
    object_id: str
    version_id: str
    sequence: int

    added_chunks: tuple[ChunkRef, ...]
    removed_chunk_ref_ids: tuple[str, ...]

    created_at: int

    def __post_init__(self) -> None:
        validate_non_empty_string(self.object_id, "object_id")
        validate_uuid(self.version_id, "version_id")
        validate_positive_integer(self.sequence, "sequence")
        validate_timestamp(self.created_at, "created_at")

        if not isinstance(self.added_chunks, tuple):
            raise TypeError("added_chunks must be a tuple")

        if not isinstance(self.removed_chunk_ref_ids, tuple):
            raise TypeError("removed_chunk_ref_ids must be a tuple")

        for chunk_ref in self.added_chunks:
            if not isinstance(chunk_ref, ChunkRef):
                raise TypeError("added_chunks must contain only ChunkRef instances")

        for chunk_ref_id in self.removed_chunk_ref_ids:
            validate_uuid(chunk_ref_id, "removed_chunk_ref_id")

        added_ids = [ref.chunk_ref_id for ref in self.added_chunks]
        added_order_keys = [ref.order_key for ref in self.added_chunks]

        if len(added_ids) != len(set(added_ids)):
            raise ValueError("added_chunks contains duplicate chunk_ref_id")

        if len(added_order_keys) != len(set(added_order_keys)):
            raise ValueError("added_chunks contains duplicate order_key")

        if len(self.removed_chunk_ref_ids) != len(set(self.removed_chunk_ref_ids)):
            raise ValueError("removed_chunk_ref_ids contains duplicates")

        overlap = set(added_ids) & set(self.removed_chunk_ref_ids)
        if overlap:
            raise ValueError("A ChunkRef cannot be added and removed in the same Delta")

        if self.sequence == 1 and self.removed_chunk_ref_ids:
            raise ValueError("The first Version cannot remove existing ChunkRefs")
