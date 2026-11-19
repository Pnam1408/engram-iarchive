from ..models import ChunkRef, VersionDelta
from ...database import ColumnFamily, KeyCodec
from ...database.serialization import deserialize
from ...repositories import (
    BaseRepository,
    CorruptMetadataError,
    RecordAlreadyExistsError,
)


class VersionDeltaRepository(BaseRepository):
    """Append-only VersionDelta persistence keyed by ObjectID + sequence."""

    _FIELDS = {
        "object_id",
        "version_id",
        "sequence",
        "added_chunks",
        "removed_chunk_ref_ids",
        "created_at",
    }

    @staticmethod
    def _to_dict(delta: VersionDelta) -> dict:
        return {
            "object_id": delta.object_id,
            "version_id": delta.version_id,
            "sequence": delta.sequence,
            "added_chunks": [
                {
                    "chunk_ref_id": ref.chunk_ref_id,
                    "chunk_id": ref.chunk_id,
                    "order_key": ref.order_key,
                }
                for ref in delta.added_chunks
            ],
            "removed_chunk_ref_ids": list(delta.removed_chunk_ref_ids),
            "created_at": delta.created_at,
        }

    @classmethod
    def _from_dict(cls, data: dict) -> VersionDelta:
        cls._require_fields(data, cls._FIELDS, "VersionDelta")
        if not isinstance(data["added_chunks"], list):
            raise ValueError("added_chunks must be a JSON array")
        if not isinstance(data["removed_chunk_ref_ids"], list):
            raise ValueError("removed_chunk_ref_ids must be a JSON array")
        added = tuple(ChunkRef(**item) for item in data["added_chunks"])
        removed = tuple(data["removed_chunk_ref_ids"])
        return VersionDelta(
            object_id=data["object_id"],
            version_id=data["version_id"],
            sequence=data["sequence"],
            added_chunks=added,
            removed_chunk_ref_ids=removed,
            created_at=data["created_at"],
        )

    def get(self, object_id: str, sequence: int) -> VersionDelta | None:
        key = KeyCodec.version_delta_key(object_id, sequence)
        try:
            data = self._get(ColumnFamily.VERSION_DELTAS, key)
            if data is None:
                return None
            return self._decode_record(data, object_id, sequence)
        except CorruptMetadataError:
            raise
        except Exception as exc:
            raise CorruptMetadataError("Cannot decode stored VersionDelta") from exc

    def get_by_version_id(self, object_id: str, version_id: str) -> VersionDelta | None:
        self._require_non_empty_string(object_id, "object_id")
        self._require_non_empty_string(version_id, "version_id")
        prefix = KeyCodec.version_delta_prefix(object_id)
        for key, raw in self.db.iter_prefix(ColumnFamily.VERSION_DELTAS, prefix):
            sequence = self._sequence_from_key(key, prefix)
            try:
                data = self._require_dict(deserialize(raw), "VersionDelta")
                delta = self._decode_record(data, object_id, sequence)
            except CorruptMetadataError:
                raise
            except Exception as exc:
                raise CorruptMetadataError("Cannot decode stored VersionDelta") from exc
            if delta.version_id == version_id:
                return delta
        return None

    def put(self, delta: VersionDelta, batch) -> None:
        if not isinstance(delta, VersionDelta):
            raise TypeError("delta must be a VersionDelta")
        key = KeyCodec.version_delta_key(delta.object_id, delta.sequence)
        if self.db.get(ColumnFamily.VERSION_DELTAS, key) is not None:
            raise RecordAlreadyExistsError(
                f"VersionDelta {delta.object_id!r}/{delta.sequence} already exists"
            )
        if self.get_by_version_id(delta.object_id, delta.version_id) is not None:
            raise RecordAlreadyExistsError(
                f"VersionID {delta.version_id!r} already has a Delta"
            )
        self._put_batch(batch, ColumnFamily.VERSION_DELTAS, key, self._to_dict(delta))

    @staticmethod
    def _sequence_from_key(key: bytes, prefix: bytes) -> int:
        try:
            if not key.startswith(prefix) or len(key) != len(prefix) + 8:
                raise CorruptMetadataError("Invalid VersionDelta storage key")
            sequence, end = KeyCodec.decode_uint64(key, len(prefix))
            if end != len(key):
                raise CorruptMetadataError("Invalid VersionDelta storage key length")
            return sequence
        except CorruptMetadataError:
            raise
        except Exception as exc:
            raise CorruptMetadataError(
                "Cannot decode VersionDelta storage key"
            ) from exc

    def _decode_record(self, data: dict, object_id: str, sequence: int) -> VersionDelta:
        try:
            delta = self._from_dict(data)
            if delta.object_id != object_id or delta.sequence != sequence:
                raise CorruptMetadataError(
                    "VersionDelta fields do not match storage key"
                )
            return delta
        except CorruptMetadataError:
            raise
        except Exception as exc:
            raise CorruptMetadataError("Cannot decode stored VersionDelta") from exc
