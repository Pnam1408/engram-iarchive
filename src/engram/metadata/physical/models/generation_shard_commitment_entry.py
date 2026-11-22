from dataclasses import dataclass

from ._validation import validate_non_negative_integer, validate_uuid
from .generation_shard_commitment import validate_field_element_bytes


@dataclass(frozen=True, slots=True)
class GenerationShardCommitmentEntry:
    generation_id: str
    shard_index: int
    shard_root: bytes

    def __post_init__(self) -> None:
        validate_uuid(self.generation_id, "generation_id")
        validate_non_negative_integer(self.shard_index, "shard_index")
        validate_field_element_bytes(self.shard_root, "shard_root")
