from ..column_families import ColumnFamily
from ..key_codec import KeyCodec
from ..serialization import serialize, deserialize
from .base import BaseRepository


class GenerationRepository(BaseRepository):
    TRANSITIONS = {
        "OPEN": {"SEALING"},
        "SEALING": {"STORED"},
        "STORED": {"AVAILABLE"},
        "AVAILABLE": set(),
    }

    def create(
        self,
        generation_id: str,
        metadata: dict | None = None,
    ) -> dict:
        self._require_non_empty_string(
            generation_id,
            "generation_id",
        )

        key = KeyCodec.generation_key(generation_id)

        if self.db.get(ColumnFamily.GENERATIONS, key) is not None:
            raise ValueError(f"Generation already exists: {generation_id}")

        record = {
            "generation_id": generation_id,
            "status": "OPEN",
            "record_count": 0,
            **(metadata or {}),
        }

        if record["generation_id"] != generation_id:
            raise ValueError("metadata cannot override generation_id")

        if record["status"] != "OPEN":
            raise ValueError("New generation must start in OPEN state")

        self._put(ColumnFamily.GENERATIONS, key, record)
        return record

    def get(self, generation_id: str) -> dict | None:
        return self._get(
            ColumnFamily.GENERATIONS,
            KeyCodec.generation_key(generation_id),
        )

    def add_record(
        self,
        generation_id: str,
        object_id: str,
        chunk_id: str,
        record_offset: int,
        record_length: int,
    ) -> dict:
        generation = self.get(generation_id)

        if generation is None:
            raise KeyError(f"Generation not found: {generation_id}")

        if generation["status"] != "OPEN":
            raise ValueError("Records can only be added to OPEN generation")

        self._require_non_negative_int(record_offset, "record_offset")
        self._require_non_negative_int(record_length, "record_length")

        if record_length == 0:
            raise ValueError("record_length must be greater than zero")

        chunk = self._get(
            ColumnFamily.CHUNKS,
            KeyCodec.encode_bytes(chunk_id),
        )

        if chunk is None:
            raise KeyError(f"Chunk not found: {chunk_id}")

        if chunk["object_id"] != object_id:
            raise ValueError("Chunk does not belong to object_id")

        record_index = generation["record_count"]

        record = {
            "generation_id": generation_id,
            "record_index": record_index,
            "object_id": object_id,
            "chunk_id": chunk_id,
            "record_offset": record_offset,
            "record_length": record_length,
        }

        record_key = KeyCodec.generation_chunk_key(
            generation_id,
            record_index,
        )

        generation["record_count"] += 1

        with self.db.batch() as batch:
            batch.put(
                ColumnFamily.GENERATION_CHUNKS,
                record_key,
                serialize(record),
            )
            batch.put(
                ColumnFamily.GENERATIONS,
                KeyCodec.generation_key(generation_id),
                serialize(generation),
            )
            batch.commit()

        return record

    def list_chunks(self, generation_id: str) -> list[dict]:
        prefix = KeyCodec.encode_bytes(generation_id)

        return [
            deserialize(value)
            for _, value in self.db.iter_prefix(
                ColumnFamily.GENERATION_CHUNKS,
                prefix,
            )
        ]

    def update_status(
        self,
        generation_id: str,
        new_status: str,
    ) -> dict:
        generation = self.get(generation_id)

        if generation is None:
            raise KeyError(f"Generation not found: {generation_id}")

        old_status = generation["status"]

        if new_status not in self.TRANSITIONS.get(old_status, set()):
            raise ValueError(
                f"Invalid generation transition: " f"{old_status} -> {new_status}"
            )

        records = self.list_chunks(generation_id)

        if new_status == "STORED":
            if not records:
                raise ValueError("Cannot store an empty generation")

            shard_records = [
                deserialize(value)
                for _, value in self.db.iter_prefix(
                    ColumnFamily.SHARDS,
                    KeyCodec.encode_bytes(generation_id),
                )
            ]

            data_shards = generation.get("data_shards")
            if data_shards is None:
                raise ValueError("Generation metadata must define data_shards")

            if len(shard_records) < data_shards:
                raise ValueError(
                    "Not enough persisted shards to reconstruct generation"
                )

        if new_status == "AVAILABLE":
            if old_status != "STORED":
                raise ValueError("Generation must be STORED first")

            # Chunk physical location được cập nhật cùng trạng thái generation.
            with self.db.batch() as batch:
                generation["status"] = new_status

                batch.put(
                    ColumnFamily.GENERATIONS,
                    KeyCodec.generation_key(generation_id),
                    serialize(generation),
                )

                for record in records:
                    chunk_key = KeyCodec.encode_bytes(record["chunk_id"])
                    chunk = self._get(ColumnFamily.CHUNKS, chunk_key)

                    if chunk is None:
                        raise KeyError(f"Chunk not found: {record['chunk_id']}")

                    chunk.update(
                        {
                            "status": "AVAILABLE",
                            "generation_id": generation_id,
                            "record_offset": record["record_offset"],
                            "record_length": record["record_length"],
                        }
                    )

                    batch.put(
                        ColumnFamily.CHUNKS,
                        chunk_key,
                        serialize(chunk),
                    )

                batch.commit()

            return generation

        generation["status"] = new_status
        self._put(
            ColumnFamily.GENERATIONS,
            KeyCodec.generation_key(generation_id),
            generation,
        )
        return generation
