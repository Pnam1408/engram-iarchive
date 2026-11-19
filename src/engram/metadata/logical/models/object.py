from dataclasses import dataclass

from ._validation import (
    validate_non_empty_string,
    validate_timestamp,
    validate_uuid,
    validate_non_negative_integer,
)


@dataclass(frozen=True, slots=True)
class Object:
    object_id: str

    head_sequence: int = 0
    head_version_id: str | None = None

    created_at: int = 0
    updated_at: int = 0

    def __post_init__(self) -> None:
        validate_non_empty_string(self.object_id, "object_id")

        validate_non_negative_integer(
            self.head_sequence,
            "head_sequence",
        )

        validate_timestamp(self.created_at, "created_at")
        validate_timestamp(self.updated_at, "updated_at")

        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not be earlier than created_at")

        if self.head_sequence == 0:
            if self.head_version_id is not None:
                raise ValueError("A new Object must not have a head_version_id")
        else:
            if self.head_version_id is None:
                raise ValueError("head_version_id is required when head_sequence > 0")

            validate_uuid(self.head_version_id, "head_version_id")
