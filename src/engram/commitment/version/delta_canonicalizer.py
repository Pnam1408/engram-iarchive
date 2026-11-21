"""Canonical encoding and hashing for logical VersionDelta records."""

from uuid import UUID

from engram.commitment.common import (
    VERSION_DELTA,
    encode_list,
    encode_string,
    encode_u64,
    encode_uuid,
    hash_with_domain,
    require_string,
)
from engram.commitment.common.exceptions import InvalidInputError
from engram.metadata.logical.models import ChunkRef, VersionDelta

DELTA_ROOT = VERSION_DELTA


def _uuid_from_model(value: str, field: str) -> UUID:
    if type(value) is not str:
        raise TypeError(f"{field} must be a UUID string in the Metadata model")
    try:
        return UUID(value)
    except (ValueError, AttributeError) as exc:
        raise InvalidInputError(f"{field} must be a valid UUID") from exc


class DeltaCanonicalizer:
    """Commit to Delta semantics; created_at is intentionally excluded."""

    @staticmethod
    def encode_chunk_ref(ref: ChunkRef) -> bytes:
        if not isinstance(ref, ChunkRef):
            raise TypeError("ref must be a ChunkRef")
        return b"".join(
            (
                encode_uuid(_uuid_from_model(ref.chunk_ref_id, "chunk_ref_id")),
                encode_uuid(_uuid_from_model(ref.chunk_id, "chunk_id")),
                encode_string(ref.order_key),
            )
        )

    @classmethod
    def encode(cls, delta: VersionDelta) -> bytes:
        if not isinstance(delta, VersionDelta):
            raise TypeError("delta must be a VersionDelta")
        require_string(delta.object_id, name="object_id", allow_empty=False)
        added = tuple(
            sorted(delta.added_chunks, key=lambda ref: ref.order_key.encode("utf-8"))
        )
        removed = tuple(
            sorted(
                (
                    _uuid_from_model(value, "removed_chunk_ref_id")
                    for value in delta.removed_chunk_ref_ids
                ),
                key=lambda value: value.bytes,
            )
        )
        return b"".join(
            (
                encode_string(delta.object_id),
                encode_uuid(_uuid_from_model(delta.version_id, "version_id")),
                encode_u64(delta.sequence),
                encode_list(added, cls.encode_chunk_ref),
                encode_list(removed, encode_uuid),
            )
        )

    @classmethod
    def hash(cls, delta: VersionDelta) -> bytes:
        return hash_with_domain(DELTA_ROOT, cls.encode(delta))
