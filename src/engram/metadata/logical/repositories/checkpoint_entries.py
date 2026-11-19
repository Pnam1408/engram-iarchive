from __future__ import annotations

from ..models import Checkpoint, CheckpointEntry
from ...database import ColumnFamily, KeyCodec
from ...database.serialization import deserialize
from ...repositories import (
    BaseRepository,
    CorruptMetadataError,
    RecordAlreadyExistsError,
)


class CheckpointEntryRepository(BaseRepository):
    """Immutable ordered snapshot entries belonging to a Checkpoint."""

    _FIELDS = {"checkpoint_id", "chunk_ref_id", "chunk_id", "order_key"}

    @staticmethod
    def _to_dict(entry: CheckpointEntry) -> dict:
        return {
            "checkpoint_id": entry.checkpoint_id,
            "chunk_ref_id": entry.chunk_ref_id,
            "chunk_id": entry.chunk_id,
            "order_key": entry.order_key,
        }

    @classmethod
    def _from_dict(cls, data: dict) -> CheckpointEntry:
        cls._require_fields(data, cls._FIELDS, "CheckpointEntry")
        return CheckpointEntry(**data)

    def list_for_checkpoint(
        self, checkpoint: Checkpoint
    ) -> tuple[CheckpointEntry, ...]:
        if not isinstance(checkpoint, Checkpoint):
            raise TypeError("checkpoint must be a Checkpoint")
        prefix = KeyCodec.checkpoint_entry_prefix(
            checkpoint.object_id, checkpoint.sequence
        )
        entries = []
        for key, raw in self.db.iter_prefix(ColumnFamily.CHECKPOINT_ENTRIES, prefix):
            suffix = key[len(prefix) :]
            try:
                order_key = KeyCodec.decode_order_key(suffix)
                data = self._require_dict(deserialize(raw), "CheckpointEntry")
                entry = self._from_dict(data)
                if entry.order_key != order_key:
                    raise CorruptMetadataError(
                        "CheckpointEntry OrderKey does not match storage key"
                    )
                if entry.checkpoint_id != checkpoint.checkpoint_id:
                    raise CorruptMetadataError(
                        "CheckpointEntry belongs to another Checkpoint"
                    )
                entries.append(entry)
            except CorruptMetadataError:
                raise
            except Exception as exc:
                raise CorruptMetadataError(
                    "Cannot decode stored CheckpointEntry"
                ) from exc

        result = tuple(entries)
        if len({entry.chunk_ref_id for entry in result}) != len(result):
            raise CorruptMetadataError("Checkpoint contains duplicate ChunkRefID")
        if any(a.order_key >= b.order_key for a, b in zip(result, result[1:])):
            raise CorruptMetadataError("Checkpoint entries are not strictly ordered")
        if len(result) != checkpoint.entry_count:
            raise CorruptMetadataError(
                f"Checkpoint entry_count mismatch: expected {checkpoint.entry_count}, found {len(result)}"
            )
        return result

    def put_all(
        self,
        checkpoint: Checkpoint,
        entries: tuple[CheckpointEntry, ...],
        batch,
    ) -> None:
        if not isinstance(checkpoint, Checkpoint):
            raise TypeError("checkpoint must be a Checkpoint")
        if not isinstance(entries, tuple) or any(
            not isinstance(e, CheckpointEntry) for e in entries
        ):
            raise TypeError("entries must be a tuple of CheckpointEntry")
        if len(entries) != checkpoint.entry_count:
            raise ValueError("entries length does not match checkpoint.entry_count")
        if batch is None:
            raise TypeError("batch is required")

        ordered = tuple(sorted(entries, key=lambda e: e.order_key))
        if len({e.chunk_ref_id for e in ordered}) != len(ordered):
            raise ValueError("entries contain duplicate ChunkRefID")
        if len({e.order_key for e in ordered}) != len(ordered):
            raise ValueError("entries contain duplicate OrderKey")

        for entry in ordered:
            if entry.checkpoint_id != checkpoint.checkpoint_id:
                raise ValueError("entry checkpoint_id does not match Checkpoint")
            key = KeyCodec.checkpoint_entry_key(
                checkpoint.object_id, checkpoint.sequence, entry.order_key
            )
            if self.db.get(ColumnFamily.CHECKPOINT_ENTRIES, key) is not None:
                raise RecordAlreadyExistsError("CheckpointEntry already exists")

        for entry in ordered:
            self._put_batch(
                batch,
                ColumnFamily.CHECKPOINT_ENTRIES,
                KeyCodec.checkpoint_entry_key(
                    checkpoint.object_id, checkpoint.sequence, entry.order_key
                ),
                self._to_dict(entry),
            )
