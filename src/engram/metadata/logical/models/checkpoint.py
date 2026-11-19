from dataclasses import dataclass

from ._validation import (
    validate_hash,
    validate_non_empty_string,
    validate_non_negative_integer,
    validate_timestamp,
    validate_uuid,
    validate_positive_integer,
)


@dataclass(frozen=True, slots=True)
class Checkpoint:
    checkpoint_id: str
    object_id: str

    sequence: int
    version_id: str

    entry_count: int
    snapshot_root: bytes

    created_at: int

    def __post_init__(self) -> None:
        validate_uuid(self.checkpoint_id, "checkpoint_id")
        validate_non_empty_string(self.object_id, "object_id")

        validate_positive_integer(self.sequence, "sequence")
        validate_uuid(self.version_id, "version_id")

        validate_non_negative_integer(
            self.entry_count,
            "entry_count",
        )

        validate_hash(self.snapshot_root, "snapshot_root")
        validate_timestamp(self.created_at, "created_at")
