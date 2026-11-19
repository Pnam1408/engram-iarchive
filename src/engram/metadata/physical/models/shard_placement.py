from dataclasses import dataclass
from enum import StrEnum

from ._validation import (
    validate_non_empty_string,
    validate_non_negative_integer,
    validate_timestamp,
    validate_uuid,
)


class PlacementStatus(StrEnum):
    PENDING = "pending"
    STORED = "stored"
    VERIFIED = "verified"
    LOST = "lost"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ShardPlacement:
    """One physical copy/location of a GenerationShard on a storage node."""

    placement_id: str

    generation_id: str
    shard_index: int

    storage_node_id: str
    storage_locator: str

    status: PlacementStatus

    created_at: int
    updated_at: int

    stored_at: int | None = None
    verified_at: int | None = None

    def __post_init__(self) -> None:
        validate_uuid(self.placement_id, "placement_id")
        validate_uuid(self.generation_id, "generation_id")
        validate_non_negative_integer(self.shard_index, "shard_index")
        validate_non_empty_string(self.storage_node_id, "storage_node_id")
        validate_non_empty_string(self.storage_locator, "storage_locator")
        if not isinstance(self.status, PlacementStatus):
            raise TypeError("status must be a PlacementStatus")

        validate_timestamp(self.created_at, "created_at")
        validate_timestamp(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not be earlier than created_at")

        if self.stored_at is not None:
            validate_timestamp(self.stored_at, "stored_at")
            if self.stored_at < self.created_at:
                raise ValueError("stored_at must not be earlier than created_at")
        if self.verified_at is not None:
            validate_timestamp(self.verified_at, "verified_at")
            if self.stored_at is None:
                raise ValueError("verified_at requires stored_at")
            if self.verified_at < self.stored_at:
                raise ValueError("verified_at must not be earlier than stored_at")

        if self.status is PlacementStatus.PENDING:
            if self.stored_at is not None or self.verified_at is not None:
                raise ValueError("PENDING placement must not have storage timestamps")
        elif self.status is PlacementStatus.STORED:
            if self.stored_at is None:
                raise ValueError("STORED placement requires stored_at")
            if self.verified_at is not None:
                raise ValueError("STORED placement must not have verified_at")
        elif self.status is PlacementStatus.VERIFIED:
            if self.stored_at is None or self.verified_at is None:
                raise ValueError(
                    "VERIFIED placement requires stored_at and verified_at"
                )
        elif self.status is PlacementStatus.LOST:
            if self.stored_at is None:
                raise ValueError("LOST placement requires stored_at")
        elif self.status is PlacementStatus.FAILED:
            if self.verified_at is not None:
                raise ValueError("FAILED placement must not have verified_at")

        lifecycle_times = [
            value for value in (self.stored_at, self.verified_at) if value is not None
        ]
        if lifecycle_times and self.updated_at < max(lifecycle_times):
            raise ValueError("updated_at must not be earlier than lifecycle timestamps")
