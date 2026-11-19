from dataclasses import dataclass

from ._validation import (
    validate_non_empty_string,
    validate_timestamp,
    validate_uuid,
)


@dataclass(frozen=True, slots=True)
class ChunkPlacement:
    """Maps a logical Chunk to one immutable record in a Generation."""

    placement_id: str

    object_id: str
    chunk_id: str

    generation_id: str
    record_id: str

    created_at: int

    def __post_init__(self) -> None:
        validate_uuid(self.placement_id, "placement_id")
        validate_non_empty_string(self.object_id, "object_id")
        validate_uuid(self.chunk_id, "chunk_id")
        validate_uuid(self.generation_id, "generation_id")
        validate_uuid(self.record_id, "record_id")
        validate_timestamp(self.created_at, "created_at")
