from dataclasses import dataclass

from ._validation import (
    validate_hash,
    validate_non_negative_integer,
    validate_positive_integer,
    validate_uuid,
)


@dataclass(frozen=True, slots=True)
class GenerationShard:
    """Immutable metadata describing one erasure-coded shard."""

    generation_id: str
    shard_index: int

    shard_size: int
    shard_hash: bytes

    def __post_init__(self) -> None:
        validate_uuid(self.generation_id, "generation_id")
        validate_non_negative_integer(self.shard_index, "shard_index")
        validate_positive_integer(self.shard_size, "shard_size")
        validate_hash(self.shard_hash, "shard_hash")
