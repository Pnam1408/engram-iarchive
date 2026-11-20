from dataclasses import dataclass

from ._validation import (
    validate_hash,
    validate_non_empty_string,
    validate_non_negative_integer,
    validate_positive_integer,
    validate_uuid,
)


@dataclass(frozen=True, slots=True)
class GenerationManifestEntry:
    """A physical record for one logical Chunk within a Generation.

    Version membership is intentionally not represented here: multiple
    Object Versions may reference the same Chunk through Logical Metadata.
    """

    generation_id: str
    record_id: str

    object_id: str
    chunk_id: str

    offset: int
    length: int

    record_hash: bytes

    def __post_init__(self) -> None:
        validate_uuid(self.generation_id, "generation_id")
        validate_uuid(self.record_id, "record_id")
        validate_non_empty_string(self.object_id, "object_id")
        validate_uuid(self.chunk_id, "chunk_id")
        validate_non_negative_integer(self.offset, "offset")
        validate_positive_integer(self.length, "length")
        validate_hash(self.record_hash, "record_hash")
