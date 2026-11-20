from dataclasses import dataclass
from enum import StrEnum

from ._validation import validate_positive_integer, validate_timestamp, validate_uuid


class GenerationStatus(StrEnum):
    OPEN = "open"
    SEALED = "sealed"
    ENCODING = "encoding"
    ENCODED = "encoded"
    STORING = "storing"
    AVAILABLE = "available"
    DEGRADED = "degraded"
    FAILED = "failed"
    LOST = "lost"


@dataclass(frozen=True, slots=True)
class Generation:
    generation_id: str

    data_shards: int
    total_shards: int

    status: GenerationStatus

    created_at: int
    updated_at: int

    sealed_at: int | None = None
    encoded_at: int | None = None
    completed_at: int | None = None

    def __post_init__(self) -> None:
        validate_uuid(self.generation_id, "generation_id")
        validate_positive_integer(self.data_shards, "data_shards")
        validate_positive_integer(self.total_shards, "total_shards")
        if self.total_shards < self.data_shards:
            raise ValueError(
                "total_shards must be greater than or equal to data_shards"
            )
        if not isinstance(self.status, GenerationStatus):
            raise TypeError("status must be a GenerationStatus")

        validate_timestamp(self.created_at, "created_at")
        validate_timestamp(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not be earlier than created_at")

        if self.sealed_at is not None:
            validate_timestamp(self.sealed_at, "sealed_at")
            if self.sealed_at < self.created_at:
                raise ValueError("sealed_at must not be earlier than created_at")
        if self.encoded_at is not None:
            validate_timestamp(self.encoded_at, "encoded_at")
            if self.sealed_at is None:
                raise ValueError("encoded_at requires sealed_at")
            if self.encoded_at < self.sealed_at:
                raise ValueError("encoded_at must not be earlier than sealed_at")

        if self.status is GenerationStatus.OPEN:
            if (
                self.sealed_at is not None
                or self.encoded_at is not None
                or self.completed_at is not None
            ):
                raise ValueError(
                    "An OPEN Generation must not have lifecycle timestamps"
                )
        elif self.status is not GenerationStatus.FAILED:
            if self.sealed_at is None:
                raise ValueError("sealed_at is required after Generation is sealed")
            if (
                self.status in {GenerationStatus.SEALED, GenerationStatus.ENCODING}
                and self.encoded_at is not None
            ):
                raise ValueError("encoded_at is not allowed before encoding completes")
            if (
                self.status
                in {
                    GenerationStatus.ENCODED,
                    GenerationStatus.STORING,
                    GenerationStatus.AVAILABLE,
                    GenerationStatus.DEGRADED,
                    GenerationStatus.LOST,
                }
                and self.encoded_at is None
            ):
                raise ValueError("encoded_at is required for this Generation status")

        if self.completed_at is not None:
            validate_timestamp(self.completed_at, "completed_at")
            if self.completed_at < self.created_at:
                raise ValueError("completed_at must not be earlier than created_at")
            if self.status not in {
                GenerationStatus.AVAILABLE,
                GenerationStatus.DEGRADED,
                GenerationStatus.FAILED,
                GenerationStatus.LOST,
            }:
                raise ValueError(
                    "completed_at is only allowed after initial completion"
                )
        elif self.status in {
            GenerationStatus.AVAILABLE,
            GenerationStatus.DEGRADED,
            GenerationStatus.FAILED,
            GenerationStatus.LOST,
        }:
            raise ValueError(
                "completed_at is required for completed Generation statuses"
            )

        lifecycle_times = [
            value
            for value in (self.sealed_at, self.encoded_at, self.completed_at)
            if value is not None
        ]
        if lifecycle_times and self.updated_at < max(lifecycle_times):
            raise ValueError("updated_at must not be earlier than lifecycle timestamps")
