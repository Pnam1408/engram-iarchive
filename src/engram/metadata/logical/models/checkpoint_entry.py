from dataclasses import dataclass

from ._validation import (
    validate_order_key,
    validate_uuid,
)


@dataclass(frozen=True, slots=True)
class CheckpointEntry:
    checkpoint_id: str

    chunk_ref_id: str
    chunk_id: str
    order_key: str

    def __post_init__(self) -> None:
        validate_uuid(self.checkpoint_id, "checkpoint_id")
        validate_uuid(self.chunk_ref_id, "chunk_ref_id")
        validate_uuid(self.chunk_id, "chunk_id")
        validate_order_key(self.order_key)
