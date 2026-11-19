from dataclasses import dataclass

from ._validation import (
    validate_non_empty_bytes,
    validate_hash,
    validate_non_empty_string,
    validate_timestamp,
    validate_uuid,
    validate_positive_integer,
)


@dataclass(frozen=True, slots=True)
class Version:
    version_id: str
    object_id: str
    sequence: int

    parent_version_id: str | None

    delta_root: bytes
    previous_version_hash: bytes | None

    codec: str

    writer_public_key: bytes
    signature: bytes

    created_at: int

    def __post_init__(self) -> None:
        validate_uuid(self.version_id, "version_id")
        validate_non_empty_string(self.object_id, "object_id")
        validate_positive_integer(self.sequence, "sequence")
        validate_timestamp(self.created_at, "created_at")

        validate_hash(self.delta_root, "delta_root")

        validate_non_empty_string(self.codec, "codec")
        validate_non_empty_bytes(
            self.writer_public_key,
            "writer_public_key",
        )
        validate_non_empty_bytes(self.signature, "signature")

        if self.sequence == 1:
            if self.parent_version_id is not None:
                raise ValueError("The first Version must not have a parent")

            if self.previous_version_hash is not None:
                raise ValueError("The first Version must not have a previous hash")
        else:
            if self.parent_version_id is None:
                raise ValueError("parent_version_id is required after Version 1")

            validate_uuid(
                self.parent_version_id,
                "parent_version_id",
            )

            if self.previous_version_hash is None:
                raise ValueError("previous_version_hash is required after Version 1")

            validate_hash(
                self.previous_version_hash,
                "previous_version_hash",
            )

        if self.parent_version_id == self.version_id:
            raise ValueError("A Version cannot be its own parent")
