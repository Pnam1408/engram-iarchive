import hashlib
import struct
from uuid import UUID

from ..models import (
    Checkpoint,
    CheckpointEntry,
    ChunkRef,
    Version,
    VersionDelta,
)
from ..models.unsigned_version import UnsignedVersion
from ..models._validation import (
    validate_non_empty_string,
    validate_uuid,
)


class CanonicalizationError(Exception):
    """Base error for canonical encoding."""


class Canonicalizer:
    """
    Deterministic binary encoding for Logical Metadata commitments.

    Encoding rules:
    - Domain-separated and versioned payloads.
    - Unsigned integers use big-endian encoding.
    - Strings and variable-length bytes use a u32 length prefix.
    - UUIDs use their 16-byte binary representation.
    - OrderKey uses ASCII.
    - SHA-256 is used for all commitments.
    """

    FORMAT_VERSION = 1

    _U32_MAX = (1 << 32) - 1
    _U64_MAX = (1 << 64) - 1

    DOMAIN_CHUNK_REF = b"ENGRAM/CHUNK_REF/v1"
    DOMAIN_DELTA = b"ENGRAM/VERSION_DELTA/v1"
    DOMAIN_VERSION_SIGNING = b"ENGRAM/VERSION_SIGNING/v1"
    DOMAIN_VERSION_RECORD = b"ENGRAM/VERSION_RECORD/v1"
    DOMAIN_CHECKPOINT_ENTRY = b"ENGRAM/CHECKPOINT_ENTRY/v1"
    DOMAIN_SNAPSHOT = b"ENGRAM/OBJECT_SNAPSHOT/v1"
    DOMAIN_CHECKPOINT = b"ENGRAM/CHECKPOINT/v1"

    # ------------------------------------------------------------------
    # Primitive encoders
    # ------------------------------------------------------------------

    @classmethod
    def _encode_u32(cls, value: int) -> bytes:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("u32 value must be an integer")

        if not 0 <= value <= cls._U32_MAX:
            raise ValueError("u32 value out of range")

        return struct.pack(">I", value)

    @classmethod
    def _encode_u64(cls, value: int) -> bytes:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("u64 value must be an integer")

        if not 0 <= value <= cls._U64_MAX:
            raise ValueError("u64 value out of range")

        return struct.pack(">Q", value)

    @classmethod
    def _encode_bytes(cls, value: bytes) -> bytes:
        if not isinstance(value, bytes):
            raise TypeError("value must be bytes")

        return cls._encode_u32(len(value)) + value

    @classmethod
    def _encode_string(cls, value: str) -> bytes:
        if not isinstance(value, str):
            raise TypeError("value must be a string")

        return cls._encode_bytes(value.encode("utf-8"))

    @staticmethod
    def _encode_uuid(value: str) -> bytes:
        if not isinstance(value, str):
            raise TypeError("UUID value must be a string")

        try:
            return UUID(value).bytes
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValueError("Invalid UUID") from exc

    @staticmethod
    def _encode_order_key(value: str) -> bytes:
        if not isinstance(value, str):
            raise TypeError("OrderKey must be a string")

        try:
            return value.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ValueError("OrderKey must contain ASCII characters") from exc

    # ------------------------------------------------------------------
    # ChunkRef
    # ------------------------------------------------------------------

    @classmethod
    def encode_chunk_ref(cls, ref: ChunkRef) -> bytes:
        if not isinstance(ref, ChunkRef):
            raise TypeError("ref must be a ChunkRef")

        order_key = cls._encode_order_key(ref.order_key)

        return b"".join(
            (
                cls.DOMAIN_CHUNK_REF,
                cls._encode_uuid(ref.chunk_ref_id),
                cls._encode_uuid(ref.chunk_id),
                cls._encode_bytes(order_key),
            )
        )

    # ------------------------------------------------------------------
    # VersionDelta
    # ------------------------------------------------------------------

    # @classmethod
    # def encode_delta(cls, delta: VersionDelta) -> bytes:
    #    if not isinstance(delta, VersionDelta):
    #        raise TypeError("delta must be a VersionDelta")
    #
    #    added = sorted(
    #        delta.added_chunks,
    #        key=lambda ref: cls._encode_order_key(ref.order_key),
    #    )
    #
    #    removed = sorted(
    #        delta.removed_chunk_ref_ids,
    #        key=cls._encode_uuid,
    #    )
    #
    #    parts = [
    #        cls.DOMAIN_DELTA,
    #        cls._encode_string(delta.object_id),
    #        cls._encode_uuid(delta.version_id),
    #        cls._encode_u64(delta.sequence),
    #        cls._encode_u64(delta.created_at),
    #        cls._encode_u32(len(added)),
    #    ]
    #
    #    for ref in added:
    #        encoded_ref = cls.encode_chunk_ref(ref)
    #        parts.append(cls._encode_bytes(encoded_ref))
    #
    #    parts.append(cls._encode_u32(len(removed)))
    #
    #    for chunk_ref_id in removed:
    #        parts.append(cls._encode_uuid(chunk_ref_id))
    #
    #    return b"".join(parts)

    # @classmethod
    # def hash_delta(cls, delta: VersionDelta) -> bytes:
    #    return hashlib.sha256(cls.encode_delta(delta)).digest()

    # ------------------------------------------------------------------
    # Version
    # ------------------------------------------------------------------

    @classmethod
    def _encode_version_fields(cls, version: Version | UnsignedVersion) -> bytes:
        if not isinstance(version, (Version, UnsignedVersion)):
            raise TypeError("version must be a Version or UnsignedVersion")

        parent = (
            b"\x00"
            if version.parent_version_id is None
            else b"\x01" + cls._encode_uuid(version.parent_version_id)
        )

        previous_hash = (
            b"\x00"
            if version.previous_version_hash is None
            else b"\x01" + version.previous_version_hash
        )

        return b"".join(
            (
                cls._encode_uuid(version.version_id),
                cls._encode_string(version.object_id),
                cls._encode_u64(version.sequence),
                parent,
                version.delta_root,
                previous_hash,
                cls._encode_string(version.codec),
                cls._encode_bytes(version.writer_public_key),
                cls._encode_u64(version.created_at),
            )
        )

    @classmethod
    def encode_version_signing_payload(
        cls,
        version: Version | UnsignedVersion,
    ) -> bytes:
        """
        Returns the exact bytes to be signed.

        Accepts UnsignedVersion before signing and Version for independent
        signature verification. The signature field is intentionally excluded.
        """
        return b"".join(
            (
                cls.DOMAIN_VERSION_SIGNING,
                cls._encode_version_fields(version),
            )
        )

    # @classmethod
    # def encode_version(cls, version: Version) -> bytes:
    #    """
    #    Returns the canonical full Version record, including signature.
    #    """
    #    return b"".join(
    #        (
    #            cls.DOMAIN_VERSION_RECORD,
    #            cls._encode_version_fields(version),
    #            cls._encode_bytes(version.signature),
    #        )
    #    )
    #
    # @classmethod
    # def hash_version(cls, version: Version) -> bytes:
    #    return hashlib.sha256(cls.encode_version(version)).digest()

    # ------------------------------------------------------------------
    # CheckpointEntry
    # ------------------------------------------------------------------

    @classmethod
    def encode_checkpoint_entry(
        cls,
        entry: CheckpointEntry,
    ) -> bytes:
        if not isinstance(entry, CheckpointEntry):
            raise TypeError("entry must be a CheckpointEntry")

        return b"".join(
            (
                cls.DOMAIN_CHECKPOINT_ENTRY,
                cls._encode_uuid(entry.checkpoint_id),
                cls._encode_uuid(entry.chunk_ref_id),
                cls._encode_uuid(entry.chunk_id),
                cls._encode_bytes(cls._encode_order_key(entry.order_key)),
            )
        )

    @classmethod
    def _encode_snapshot_entry(
        cls,
        entry: CheckpointEntry,
    ) -> bytes:
        """
        Encodes snapshot state without checkpoint_id.

        checkpoint_id identifies the storage record, not the Object state.
        """
        return b"".join(
            (
                cls._encode_uuid(entry.chunk_ref_id),
                cls._encode_uuid(entry.chunk_id),
                cls._encode_bytes(cls._encode_order_key(entry.order_key)),
            )
        )

    # ------------------------------------------------------------------
    # Object snapshot
    # ------------------------------------------------------------------

    @classmethod
    def encode_snapshot(
        cls,
        *,
        object_id: str,
        sequence: int,
        version_id: str,
        entries: tuple[CheckpointEntry, ...],
    ) -> bytes:
        validate_non_empty_string(object_id, "object_id")

        validate_uuid(version_id, "version_id")

        if isinstance(sequence, bool) or not isinstance(sequence, int):
            raise TypeError("sequence must be an integer")

        if sequence <= 0:
            raise ValueError("sequence must be positive")

        if not isinstance(entries, tuple):
            raise TypeError("entries must be a tuple")

        if entries and any(
            entry.checkpoint_id != entries[0].checkpoint_id for entry in entries
        ):
            raise CanonicalizationError(
                "All entries must belong to the same Checkpoint"
            )

        sorted_entries = tuple(
            sorted(
                entries,
                key=lambda entry: cls._encode_order_key(entry.order_key),
            )
        )

        ref_ids = [entry.chunk_ref_id for entry in sorted_entries]
        order_keys = [entry.order_key for entry in sorted_entries]

        if len(ref_ids) != len(set(ref_ids)):
            raise CanonicalizationError("Snapshot contains duplicate ChunkRefID")

        if len(order_keys) != len(set(order_keys)):
            raise CanonicalizationError("Snapshot contains duplicate OrderKey")

        for entry in sorted_entries:
            if entry.checkpoint_id != entries[0].checkpoint_id if entries else False:
                raise CanonicalizationError(
                    "All entries must belong to the same Checkpoint"
                )

        parts = [
            cls.DOMAIN_SNAPSHOT,
            cls._encode_string(object_id),
            cls._encode_u64(sequence),
            cls._encode_uuid(version_id),
            cls._encode_u64(len(sorted_entries)),
        ]

        for entry in sorted_entries:
            parts.append(cls._encode_bytes(cls._encode_snapshot_entry(entry)))

        return b"".join(parts)

    @classmethod
    def hash_snapshot(
        cls,
        *,
        object_id: str,
        sequence: int,
        version_id: str,
        entries: tuple[CheckpointEntry, ...],
    ) -> bytes:
        return hashlib.sha256(
            cls.encode_snapshot(
                object_id=object_id,
                sequence=sequence,
                version_id=version_id,
                entries=entries,
            )
        ).digest()

    # ------------------------------------------------------------------
    # Checkpoint
    # ------------------------------------------------------------------

    @classmethod
    def encode_checkpoint(cls, checkpoint: Checkpoint) -> bytes:
        if not isinstance(checkpoint, Checkpoint):
            raise TypeError("checkpoint must be a Checkpoint")

        return b"".join(
            (
                cls.DOMAIN_CHECKPOINT,
                cls._encode_uuid(checkpoint.checkpoint_id),
                cls._encode_string(checkpoint.object_id),
                cls._encode_u64(checkpoint.sequence),
                cls._encode_uuid(checkpoint.version_id),
                cls._encode_u64(checkpoint.entry_count),
                checkpoint.snapshot_root,
                cls._encode_u64(checkpoint.created_at),
            )
        )

    @classmethod
    def hash_checkpoint(cls, checkpoint: Checkpoint) -> bytes:
        return hashlib.sha256(cls.encode_checkpoint(checkpoint)).digest()
