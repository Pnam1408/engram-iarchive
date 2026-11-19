from ..database.column_families import ColumnFamily
from ..database.key_codec import KeyCodec
from ..database.serialization import serialize, deserialize
from .base import BaseRepository


class ChunkRepository(BaseRepository):
    PROTECTED_FIELDS = {
        "chunk_id",
        "object_id",
        "status",
        "generation_id",
        "record_offset",
        "record_length",
    }

    def create(
        self,
        chunk_id: str,
        object_id: str,
        metadata: dict | None = None,
    ) -> dict:
        self._require_non_empty_string(chunk_id, "chunk_id")
        self._require_non_empty_string(object_id, "object_id")
        extra = self._require_metadata(metadata, self.PROTECTED_FIELDS)
        key = KeyCodec.encode_bytes(chunk_id)

        if self.db.get(ColumnFamily.CHUNKS, key) is not None:
            raise ValueError(f"Chunk already exists: {chunk_id}")

        obj = self._get(ColumnFamily.OBJECTS, KeyCodec.object_key(object_id))
        if obj is None:
            raise KeyError(f"Object not found: {object_id}")
        if obj.get("status") != "ACTIVE":
            raise ValueError(f"Object is not ACTIVE: {object_id}")

        record = {
            **extra,
            "chunk_id": chunk_id,
            "object_id": object_id,
            "status": "PENDING",
        }
        self._put(ColumnFamily.CHUNKS, key, record)
        return record

    def get(self, chunk_id: str) -> dict | None:
        return self._get(ColumnFamily.CHUNKS, KeyCodec.encode_bytes(chunk_id))

    def update(self, chunk_id: str, changes: dict) -> dict:
        if not isinstance(changes, dict):
            raise TypeError("changes must be a dict")
        if self.PROTECTED_FIELDS.intersection(changes):
            raise ValueError(
                "Chunk identity, lifecycle and location are managed internally"
            )

        record = self.get(chunk_id)
        if record is None:
            raise KeyError(f"Chunk not found: {chunk_id}")
        record.update(changes)
        self._put(ColumnFamily.CHUNKS, KeyCodec.encode_bytes(chunk_id), record)
        return record

    def get_reference_count(self, chunk_id: str) -> int:
        raw = self.db.get(ColumnFamily.CHUNK_REFS, KeyCodec.encode_bytes(chunk_id))
        if raw is None:
            return 0
        value = deserialize(raw)
        count = value.get("reference_count")
        self._require_non_negative_int(count, "reference_count")
        return count

    def increment_ref(self, chunk_id: str, amount: int = 1) -> int:
        self._require_non_negative_int(amount, "amount")
        if self.get(chunk_id) is None:
            raise KeyError(f"Chunk not found: {chunk_id}")
        if amount == 0:
            return self.get_reference_count(chunk_id)

        key = KeyCodec.encode_bytes(chunk_id)
        current = self.get_reference_count(chunk_id)
        updated = current + amount
        with self.db.batch() as batch:
            batch.put(
                ColumnFamily.CHUNK_REFS,
                key,
                serialize({"chunk_id": chunk_id, "reference_count": updated}),
            )
            batch.commit()
        return updated

    def decrement_ref(self, chunk_id: str, amount: int = 1) -> int:
        self._require_non_negative_int(amount, "amount")
        if self.get(chunk_id) is None:
            raise KeyError(f"Chunk not found: {chunk_id}")
        current = self.get_reference_count(chunk_id)
        if amount > current:
            raise ValueError("Reference count cannot become negative")
        if amount == 0:
            return current

        key = KeyCodec.encode_bytes(chunk_id)
        updated = current - amount
        with self.db.batch() as batch:
            if updated == 0:
                batch.delete(ColumnFamily.CHUNK_REFS, key)
            else:
                batch.put(
                    ColumnFamily.CHUNK_REFS,
                    key,
                    serialize({"chunk_id": chunk_id, "reference_count": updated}),
                )
            batch.commit()
        return updated
