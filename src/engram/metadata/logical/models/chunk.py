from dataclasses import dataclass

from ._validation import (
    validate_hash,
    validate_non_empty_string,
    validate_positive_integer,
    validate_timestamp,
    validate_uuid,
)


@dataclass(frozen=True, slots=True)
class Chunk:
    object_id: str
    chunk_id: str

    content_hash: bytes
    plaintext_size: int

    created_at: int

    def __post_init__(self) -> None:
        validate_non_empty_string(self.object_id, "object_id")
        validate_uuid(self.chunk_id, "chunk_id")

        validate_hash(self.content_hash, "content_hash")
        validate_positive_integer(
            self.plaintext_size,
            "plaintext_size",
        )

        validate_timestamp(self.created_at, "created_at")
