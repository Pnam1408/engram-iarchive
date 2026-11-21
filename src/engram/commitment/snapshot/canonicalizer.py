"""Canonical encoding for Object Snapshot commitments."""

from uuid import UUID

from engram.commitment.common import (
    SNAPSHOT,
    encode_list,
    encode_raw_bytes,
    encode_string,
    encode_u64,
    encode_uuid,
    hash_with_domain,
)
from engram.commitment.common.exceptions import InvalidInputError
from engram.metadata.logical.models import ChunkRef


def _uuid_from_model(value: str, field: str) -> UUID:
    if type(value) is not str:
        raise TypeError(f"{field} must be a UUID string in the Metadata model")
    try:
        return UUID(value)
    except (ValueError, AttributeError) as exc:
        raise InvalidInputError(f"{field} must be a valid UUID") from exc


class SnapshotCanonicalizer:
    """Canonicalizes a complete, materialized ChunkRef state.

    The checkpoint identifier and all timestamps are deliberately excluded:
    they identify persistence/audit records, not the logical Object state.
    """

    @staticmethod
    def _encode_chunk_ref(ref: ChunkRef) -> bytes:
        if not isinstance(ref, ChunkRef):
            raise TypeError("snapshot entries must be ChunkRef instances")
        return b"".join(
            (
                encode_uuid(_uuid_from_model(ref.chunk_ref_id, "chunk_ref_id")),
                encode_uuid(_uuid_from_model(ref.chunk_id, "chunk_id")),
                encode_string(ref.order_key),
            )
        )

    @classmethod
    def encode(
        cls,
        *,
        object_id: str,
        sequence: int,
        version_id: str,
        chunk_refs: tuple[ChunkRef, ...],
    ) -> bytes:
        if type(object_id) is not str or not object_id:
            raise ValueError("object_id must be a non-empty string")
        if type(sequence) is not int or sequence <= 0:
            raise ValueError("sequence must be a positive integer")
        if type(chunk_refs) is not tuple:
            raise TypeError("chunk_refs must be a tuple")
        if any(not isinstance(ref, ChunkRef) for ref in chunk_refs):
            raise TypeError("chunk_refs must contain only ChunkRef instances")

        ordered = tuple(
            sorted(chunk_refs, key=lambda ref: ref.order_key.encode("utf-8"))
        )
        ref_ids = tuple(ref.chunk_ref_id for ref in ordered)
        order_keys = tuple(ref.order_key for ref in ordered)
        if len(ref_ids) != len(set(ref_ids)):
            raise InvalidInputError("snapshot contains duplicate ChunkRefID")
        if len(order_keys) != len(set(order_keys)):
            raise InvalidInputError("snapshot contains duplicate OrderKey")

        return b"".join(
            (
                encode_string(object_id),
                encode_u64(sequence),
                encode_uuid(_uuid_from_model(version_id, "version_id")),
                encode_list(ordered, cls._encode_chunk_ref),
            )
        )

    @classmethod
    def hash(
        cls,
        *,
        object_id: str,
        sequence: int,
        version_id: str,
        chunk_refs: tuple[ChunkRef, ...],
    ) -> bytes:
        return hash_with_domain(
            SNAPSHOT,
            cls.encode(
                object_id=object_id,
                sequence=sequence,
                version_id=version_id,
                chunk_refs=chunk_refs,
            ),
        )
