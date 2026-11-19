from __future__ import annotations

import hashlib

from ..models import ChunkRef
from ...database import ColumnFamily, KeyCodec
from ...database.serialization import deserialize
from ...repositories import (
    BaseRepository,
    CorruptMetadataError,
    RecordAlreadyExistsError,
)


class ObjectChunkIndexError(Exception):
    """Base error for Object Chunk Index operations."""


class ObjectChunkIndexSequenceConflictError(ObjectChunkIndexError):
    """The materialized index sequence does not match the Object Head."""


class ObjectChunkIndexRepository(BaseRepository):
    """
    Mutable materialized index of the current Object Head.

    Entry keys are (ObjectID, OrderKey). A reserved per-Object state key in the
    same Column Family stores the Version sequence represented by the index.
    This state marker is necessary even when the Object contains zero chunks.
    """

    _ENTRY_FIELDS = {"object_id", "chunk_ref_id", "chunk_id", "order_key"}
    _STATE_FIELDS = {"object_id", "sequence"}

    @staticmethod
    def _entry_dict(object_id: str, ref: ChunkRef) -> dict:
        return {
            "object_id": object_id,
            "chunk_ref_id": ref.chunk_ref_id,
            "chunk_id": ref.chunk_id,
            "order_key": ref.order_key,
        }

    def _decode_entry(
        self,
        raw: bytes,
        *,
        object_id: str,
        order_key: str,
    ) -> ChunkRef:
        try:
            data = self._require_dict(deserialize(raw), "ObjectChunkIndexEntry")
            self._require_fields(data, self._ENTRY_FIELDS, "ObjectChunkIndexEntry")
            ref = ChunkRef(
                chunk_ref_id=data["chunk_ref_id"],
                chunk_id=data["chunk_id"],
                order_key=data["order_key"],
            )
            if data["object_id"] != object_id or ref.order_key != order_key:
                raise CorruptMetadataError(
                    "Object Chunk Index value does not match storage key"
                )
            return ref
        except CorruptMetadataError:
            raise
        except Exception as exc:
            raise CorruptMetadataError(
                "Cannot decode Object Chunk Index entry"
            ) from exc

    def list_for_object(self, object_id: str) -> tuple[ChunkRef, ...]:
        self._require_non_empty_string(object_id, "object_id")
        prefix = KeyCodec.object_chunk_index_prefix(object_id)
        state_key = KeyCodec.object_chunk_index_state_key(object_id)
        entries: list[ChunkRef] = []

        for key, raw in self.db.iter_prefix(ColumnFamily.OBJECT_CHUNK_INDEX, prefix):
            if key == state_key:
                continue
            suffix = key[len(prefix) :]
            try:
                order_key = KeyCodec.decode_order_key(suffix)
            except Exception as exc:
                raise CorruptMetadataError(
                    "Invalid Object Chunk Index storage key"
                ) from exc
            entries.append(
                self._decode_entry(
                    raw,
                    object_id=object_id,
                    order_key=order_key,
                )
            )

        # RocksDB bytewise ordering and fractional OrderKey ASCII ordering agree.
        result = tuple(entries)
        if any(
            left.order_key >= right.order_key for left, right in zip(result, result[1:])
        ):
            raise CorruptMetadataError(
                "Object Chunk Index entries are not strictly ordered"
            )
        if len({ref.chunk_ref_id for ref in result}) != len(result):
            raise CorruptMetadataError(
                "Object Chunk Index contains duplicate ChunkRefID"
            )
        return result

    def get_sequence(self, object_id: str) -> int:
        self._require_non_empty_string(object_id, "object_id")
        key = KeyCodec.object_chunk_index_state_key(object_id)
        raw = self.db.get(ColumnFamily.OBJECT_CHUNK_INDEX, key)
        if raw is None:
            if self.list_for_object(object_id):
                raise CorruptMetadataError(
                    "Object Chunk Index entries exist without a state marker"
                )
            return 0

        try:
            data = self._require_dict(deserialize(raw), "ObjectChunkIndexState")
            self._require_fields(data, self._STATE_FIELDS, "ObjectChunkIndexState")
            if data["object_id"] != object_id:
                raise CorruptMetadataError(
                    "Object Chunk Index state does not match storage key"
                )
            sequence = data["sequence"]
            if (
                isinstance(sequence, bool)
                or not isinstance(sequence, int)
                or sequence < 0
            ):
                raise ValueError("sequence must be a non-negative integer")
            if sequence == 0 and self.list_for_object(object_id):
                raise CorruptMetadataError(
                    "Object Chunk Index has entries at sequence zero"
                )
            return sequence
        except CorruptMetadataError:
            raise
        except Exception as exc:
            raise CorruptMetadataError(
                "Cannot decode Object Chunk Index state"
            ) from exc

    def get_by_order_key(
        self,
        object_id: str,
        order_key: str,
    ) -> ChunkRef | None:
        self._require_non_empty_string(object_id, "object_id")
        key = KeyCodec.object_chunk_index_key(object_id, order_key)
        raw = self.db.get(ColumnFamily.OBJECT_CHUNK_INDEX, key)
        if raw is None:
            return None
        return self._decode_entry(raw, object_id=object_id, order_key=order_key)

    def stage_changes(
        self,
        *,
        object_id: str,
        expected_sequence: int,
        new_sequence: int,
        removed_refs: tuple[ChunkRef, ...],
        added_refs: tuple[ChunkRef, ...],
        batch,
    ) -> None:
        """
        Stage an incremental index update and its sequence marker.

        A removed OrderKey may be reused by an added ChunkRef in the same
        Delta. The corresponding delete is staged before the replacement put.
        """
        self._require_non_empty_string(object_id, "object_id")
        self._require_non_negative_int(expected_sequence, "expected_sequence")
        self._require_non_negative_int(new_sequence, "new_sequence")
        if new_sequence != expected_sequence + 1:
            raise ValueError("new_sequence must equal expected_sequence + 1")
        if not isinstance(removed_refs, tuple) or not isinstance(added_refs, tuple):
            raise TypeError("removed_refs and added_refs must be tuples")
        if any(not isinstance(ref, ChunkRef) for ref in removed_refs + added_refs):
            raise TypeError("index changes must contain only ChunkRef instances")

        actual_sequence = self.get_sequence(object_id)
        if actual_sequence != expected_sequence:
            raise ObjectChunkIndexSequenceConflictError(
                f"Expected index sequence {expected_sequence}, found {actual_sequence}"
            )

        current = self.list_for_object(object_id)
        current_by_id = {ref.chunk_ref_id: ref for ref in current}
        current_by_key = {ref.order_key: ref for ref in current}

        removed_ids = [ref.chunk_ref_id for ref in removed_refs]
        removed_keys = [ref.order_key for ref in removed_refs]
        if len(removed_ids) != len(set(removed_ids)):
            raise ValueError("removed_refs contains duplicate ChunkRefID")
        if len(removed_keys) != len(set(removed_keys)):
            raise ValueError("removed_refs contains duplicate OrderKey")

        for ref in removed_refs:
            if current_by_id.get(ref.chunk_ref_id) != ref:
                raise ObjectChunkIndexError(
                    f"Removed ChunkRef is not present in index: {ref.chunk_ref_id}"
                )

        added_ids = [ref.chunk_ref_id for ref in added_refs]
        added_keys = [ref.order_key for ref in added_refs]
        if len(added_ids) != len(set(added_ids)):
            raise ValueError("added_refs contains duplicate ChunkRefID")
        if len(added_keys) != len(set(added_keys)):
            raise ValueError("added_refs contains duplicate OrderKey")

        removed_id_set = set(removed_ids)
        removed_key_set = set(removed_keys)
        retained_ids = set(current_by_id) - removed_id_set

        for ref in added_refs:
            if ref.chunk_ref_id in retained_ids:
                raise ObjectChunkIndexError(
                    f"Added ChunkRefID already exists: {ref.chunk_ref_id}"
                )
            existing = current_by_key.get(ref.order_key)
            if existing is not None and ref.order_key not in removed_key_set:
                raise ObjectChunkIndexError(
                    f"Added OrderKey already exists: {ref.order_key}"
                )

        if batch is None:
            raise TypeError("batch is required")

        for ref in removed_refs:
            batch.delete(
                ColumnFamily.OBJECT_CHUNK_INDEX,
                KeyCodec.object_chunk_index_key(object_id, ref.order_key),
            )

        for ref in added_refs:
            self._put_batch(
                batch,
                ColumnFamily.OBJECT_CHUNK_INDEX,
                KeyCodec.object_chunk_index_key(object_id, ref.order_key),
                self._entry_dict(object_id, ref),
            )

        self._put_batch(
            batch,
            ColumnFamily.OBJECT_CHUNK_INDEX,
            KeyCodec.object_chunk_index_state_key(object_id),
            {"object_id": object_id, "sequence": new_sequence},
        )

    def snapshot_token(self, object_id: str) -> bytes:
        """Return a deterministic fingerprint of all raw index records for an Object."""
        self._require_non_empty_string(object_id, "object_id")
        prefix = KeyCodec.object_chunk_index_prefix(object_id)
        digest = hashlib.sha256(b"ENGRAM/OBJECT_CHUNK_INDEX_STATE/v1")
        for key, value in self.db.iter_prefix(ColumnFamily.OBJECT_CHUNK_INDEX, prefix):
            digest.update(len(key).to_bytes(8, "big"))
            digest.update(key)
            digest.update(len(value).to_bytes(8, "big"))
            digest.update(value)
        return digest.digest()

    def stage_replacement(
        self,
        *,
        object_id: str,
        expected_snapshot_token: bytes,
        new_sequence: int,
        refs: tuple[ChunkRef, ...],
        batch,
    ) -> None:
        """
        Stage a full replacement, even when existing index records are corrupt.

        The raw snapshot token provides optimistic concurrency detection without
        requiring the old entries or state marker to decode successfully.
        """
        self._require_non_empty_string(object_id, "object_id")
        if not isinstance(expected_snapshot_token, bytes) or len(expected_snapshot_token) != 32:
            raise ValueError("expected_snapshot_token must be a 32-byte digest")
        self._require_non_negative_int(new_sequence, "new_sequence")
        if not isinstance(refs, tuple) or any(
            not isinstance(ref, ChunkRef) for ref in refs
        ):
            raise TypeError("refs must be a tuple of ChunkRef")
        if batch is None:
            raise TypeError("batch is required")

        if self.snapshot_token(object_id) != expected_snapshot_token:
            raise ObjectChunkIndexSequenceConflictError(
                "Object Chunk Index changed before replacement"
            )

        order_keys = [ref.order_key for ref in refs]
        ref_ids = [ref.chunk_ref_id for ref in refs]
        if len(order_keys) != len(set(order_keys)):
            raise ValueError("replacement contains duplicate OrderKey")
        if len(ref_ids) != len(set(ref_ids)):
            raise ValueError("replacement contains duplicate ChunkRefID")
        if any(left >= right for left, right in zip(order_keys, order_keys[1:])):
            raise ValueError("refs must be strictly ordered by OrderKey")

        prefix = KeyCodec.object_chunk_index_prefix(object_id)
        # Delete raw keys rather than decoding them: rebuilding must be able to
        # repair malformed entries and a malformed state marker.
        for key, _ in self.db.iter_prefix(ColumnFamily.OBJECT_CHUNK_INDEX, prefix):
            batch.delete(ColumnFamily.OBJECT_CHUNK_INDEX, key)

        for ref in refs:
            self._put_batch(
                batch,
                ColumnFamily.OBJECT_CHUNK_INDEX,
                KeyCodec.object_chunk_index_key(object_id, ref.order_key),
                self._entry_dict(object_id, ref),
            )

        self._put_batch(
            batch,
            ColumnFamily.OBJECT_CHUNK_INDEX,
            KeyCodec.object_chunk_index_state_key(object_id),
            {"object_id": object_id, "sequence": new_sequence},
        )
