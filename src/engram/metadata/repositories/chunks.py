from ..column_families import ColumnFamily
from ..key_codec import KeyCodec
from ..serialization import serialize, deserialize
from .base import BaseRepository


class ChunkRepository(BaseRepository):
    def create(
        self,
        chunk_id: str,
        object_id: str,
        metadata: dict | None = None,
    ) -> dict:
        self._require_non_empty_string(chunk_id, "chunk_id")
        self._require_non_empty_string(object_id, "object_id")

        key = KeyCodec.encode_bytes(chunk_id)

        if self.db.get(ColumnFamily.CHUNKS, key) is not None:
            raise ValueError(f"Chunk already exists: {chunk_id}")

        record = {
            "chunk_id": chunk_id,
            "object_id": object_id,
            "status": "PENDING",
            **(metadata or {}),
        }

        if record["chunk_id"] != chunk_id:
            raise ValueError("metadata cannot override chunk_id")

        if record["object_id"] != object_id:
            raise ValueError("metadata cannot override object_id")

        self._put(ColumnFamily.CHUNKS, key, record)
        return record

    def get(self, chunk_id: str) -> dict | None:
        return self._get(
            ColumnFamily.CHUNKS,
            KeyCodec.encode_bytes(chunk_id),
        )

    def update(self, chunk_id: str, changes: dict) -> dict:
        record = self.get(chunk_id)

        if record is None:
            raise KeyError(f"Chunk not found: {chunk_id}")

        if {"chunk_id", "object_id"}.intersection(changes):
            raise ValueError("Cannot modify chunk identity")

        record.update(changes)
        self._put(
            ColumnFamily.CHUNKS,
            KeyCodec.encode_bytes(chunk_id),
            record,
        )
        return record

    def get_reference_count(self, chunk_id: str) -> int:
        raw = self.db.get(
            ColumnFamily.CHUNK_REFS,
            KeyCodec.encode_bytes(chunk_id),
        )

        if raw is None:
            return 0

        return deserialize(raw)["reference_count"]

    def increment_ref(self, chunk_id: str, amount: int = 1) -> int:
        self._require_non_negative_int(amount, "amount")

        if amount == 0:
            return self.get_reference_count(chunk_id)

        if self.get(chunk_id) is None:
            raise KeyError(f"Chunk not found: {chunk_id}")

        key = KeyCodec.encode_bytes(chunk_id)
        current = self.get_reference_count(chunk_id)
        updated = current + amount

        with self.db.batch() as batch:
            batch.put(
                ColumnFamily.CHUNK_REFS,
                key,
                serialize(
                    {
                        "chunk_id": chunk_id,
                        "reference_count": updated,
                    }
                ),
            )
            batch.commit()

        return updated

    def decrement_ref(self, chunk_id: str, amount: int = 1) -> int:
        self._require_non_negative_int(amount, "amount")

        if amount == 0:
            return self.get_reference_count(chunk_id)

        if self.get(chunk_id) is None:
            raise KeyError(f"Chunk not found: {chunk_id}")

        key = KeyCodec.encode_bytes(chunk_id)
        current = self.get_reference_count(chunk_id)

        if amount > current:
            raise ValueError("Reference count cannot become negative")

        updated = current - amount

        with self.db.batch() as batch:
            if updated == 0:
                batch.delete(ColumnFamily.CHUNK_REFS, key)
            else:
                batch.put(
                    ColumnFamily.CHUNK_REFS,
                    key,
                    serialize(
                        {
                            "chunk_id": chunk_id,
                            "reference_count": updated,
                        }
                    ),
                )
            batch.commit()

        return updated
