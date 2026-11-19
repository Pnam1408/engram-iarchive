from __future__ import annotations

from ..models import Checkpoint
from ...database import ColumnFamily, KeyCodec
from ...database.serialization import decode_bytes, encode_bytes
from ...repositories import (
    BaseRepository,
    CorruptMetadataError,
    RecordAlreadyExistsError,
)


class CheckpointRepository(BaseRepository):
    """Immutable Checkpoint records keyed by (ObjectID, sequence)."""

    _FIELDS = {
        "checkpoint_id",
        "object_id",
        "sequence",
        "version_id",
        "entry_count",
        "snapshot_root",
        "created_at",
    }

    @staticmethod
    def _to_dict(checkpoint: Checkpoint) -> dict:
        return {
            "checkpoint_id": checkpoint.checkpoint_id,
            "object_id": checkpoint.object_id,
            "sequence": checkpoint.sequence,
            "version_id": checkpoint.version_id,
            "entry_count": checkpoint.entry_count,
            "snapshot_root": encode_bytes(checkpoint.snapshot_root),
            "created_at": checkpoint.created_at,
        }

    @classmethod
    def _from_dict(cls, data: dict) -> Checkpoint:
        cls._require_fields(data, cls._FIELDS, "Checkpoint")
        return Checkpoint(
            checkpoint_id=data["checkpoint_id"],
            object_id=data["object_id"],
            sequence=data["sequence"],
            version_id=data["version_id"],
            entry_count=data["entry_count"],
            snapshot_root=decode_bytes(data["snapshot_root"], "snapshot_root"),
            created_at=data["created_at"],
        )

    @staticmethod
    def _sequence_from_key(key: bytes, prefix: bytes) -> int:
        if not key.startswith(prefix) or len(key) != len(prefix) + 8:
            raise CorruptMetadataError("Invalid Checkpoint storage key")
        try:
            sequence, end = KeyCodec.decode_uint64(key, len(prefix))
        except Exception as exc:
            raise CorruptMetadataError("Cannot decode Checkpoint key") from exc
        if end != len(key) or sequence <= 0:
            raise CorruptMetadataError("Invalid Checkpoint sequence in key")
        return sequence

    def _decode(self, raw: bytes, object_id: str, sequence: int) -> Checkpoint:
        try:
            from ...database.serialization import deserialize

            data = self._require_dict(deserialize(raw), "Checkpoint")
            checkpoint = self._from_dict(data)
            if checkpoint.object_id != object_id or checkpoint.sequence != sequence:
                raise CorruptMetadataError("Checkpoint fields do not match storage key")
            return checkpoint
        except CorruptMetadataError:
            raise
        except Exception as exc:
            raise CorruptMetadataError("Cannot decode stored Checkpoint") from exc

    def get(self, object_id: str, sequence: int) -> Checkpoint | None:
        self._require_non_empty_string(object_id, "object_id")
        self._require_non_negative_int(sequence, "sequence")
        if sequence == 0:
            raise ValueError("Checkpoint sequence must be positive")
        raw = self.db.get(
            ColumnFamily.CHECKPOINTS, KeyCodec.checkpoint_key(object_id, sequence)
        )
        return None if raw is None else self._decode(raw, object_id, sequence)

    def list_for_object(self, object_id: str) -> tuple[Checkpoint, ...]:
        self._require_non_empty_string(object_id, "object_id")
        prefix = KeyCodec.checkpoint_prefix(object_id)
        result = []
        for key, raw in self.db.iter_prefix(ColumnFamily.CHECKPOINTS, prefix):
            sequence = self._sequence_from_key(key, prefix)
            result.append(self._decode(raw, object_id, sequence))
        return tuple(result)

    def latest_before(self, object_id: str, max_sequence: int) -> Checkpoint | None:
        self._require_non_empty_string(object_id, "object_id")
        self._require_non_negative_int(max_sequence, "max_sequence")
        candidates = [
            cp for cp in self.list_for_object(object_id) if cp.sequence <= max_sequence
        ]
        return candidates[-1] if candidates else None

    def put(self, checkpoint: Checkpoint, batch) -> None:
        if not isinstance(checkpoint, Checkpoint):
            raise TypeError("checkpoint must be a Checkpoint")
        key = KeyCodec.checkpoint_key(checkpoint.object_id, checkpoint.sequence)
        if self.db.get(ColumnFamily.CHECKPOINTS, key) is not None:
            raise RecordAlreadyExistsError(
                f"Checkpoint {checkpoint.object_id!r}/{checkpoint.sequence} already exists"
            )
        for existing in self.list_for_object(checkpoint.object_id):
            if existing.checkpoint_id == checkpoint.checkpoint_id:
                raise RecordAlreadyExistsError(
                    f"CheckpointID {checkpoint.checkpoint_id!r} already exists"
                )
        self._put_batch(batch, ColumnFamily.CHECKPOINTS, key, self._to_dict(checkpoint))
