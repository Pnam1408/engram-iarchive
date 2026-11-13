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
    PROTECTED_FIELDS = {"generation_id", "status", "record_count"}

    def create(self, generation_id: str, metadata: dict | None = None) -> dict:
        self._require_non_empty_string(generation_id, "generation_id")
        extra = self._require_metadata(metadata, self.PROTECTED_FIELDS)

        data_shards = extra.get("data_shards")
        total_shards = extra.get("total_shards")
        int_data_shards = self._require_non_negative_int(data_shards, "data_shards")
        int_total_shards = self._require_non_negative_int(total_shards, "total_shards")
        if int_data_shards < 1 or int_total_shards < int_data_shards:
            raise ValueError("Require 1 <= data_shards <= total_shards")

        key = KeyCodec.generation_key(generation_id)
        if self.db.get(ColumnFamily.GENERATIONS, key) is not None:
            raise ValueError(f"Generation already exists: {generation_id}")

        record = {
            **extra,
            "generation_id": generation_id,
            "status": "OPEN",
            "record_count": 0,
        }
        self._put(ColumnFamily.GENERATIONS, key, record)
        return record

    def get(self, generation_id: str) -> dict | None:
        return self._get(
            ColumnFamily.GENERATIONS, KeyCodec.generation_key(generation_id)
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

        self._require_non_empty_string(object_id, "object_id")
        self._require_non_empty_string(chunk_id, "chunk_id")
        self._require_non_negative_int(record_offset, "record_offset")
        self._require_non_negative_int(record_length, "record_length")
        if record_length == 0:
            raise ValueError("record_length must be greater than zero")

        chunk_key = KeyCodec.encode_bytes(chunk_id)
        chunk = self._get(ColumnFamily.CHUNKS, chunk_key)
        if chunk is None:
            raise KeyError(f"Chunk not found: {chunk_id}")
        if chunk["object_id"] != object_id:
            raise ValueError("Chunk does not belong to object_id")
        if chunk["status"] != "PENDING":
            raise ValueError(
                f"Chunk must be PENDING before assignment; got {chunk['status']}"
            )

        records = self.list_chunks(generation_id)
        if any(item["chunk_id"] == chunk_id for item in records):
            raise ValueError(f"Chunk already exists in generation: {chunk_id}")

        start, end = record_offset, record_offset + record_length
        for item in records:
            other_start = item["record_offset"]
            other_end = other_start + item["record_length"]
            if start < other_end and other_start < end:
                raise ValueError("Generation record ranges must not overlap")

        record_index = generation["record_count"]
        record = {
            "generation_id": generation_id,
            "record_index": record_index,
            "object_id": object_id,
            "chunk_id": chunk_id,
            "record_offset": record_offset,
            "record_length": record_length,
        }
        generation["record_count"] += 1
        chunk.update(
            {
                "status": "ASSIGNED",
                "generation_id": generation_id,
                "record_offset": record_offset,
                "record_length": record_length,
            }
        )

        with self.db.batch() as batch:
            batch.put(
                ColumnFamily.GENERATION_CHUNKS,
                KeyCodec.generation_chunk_key(generation_id, record_index),
                serialize(record),
            )
            batch.put(
                ColumnFamily.GENERATIONS,
                KeyCodec.generation_key(generation_id),
                serialize(generation),
            )
            batch.put(ColumnFamily.CHUNKS, chunk_key, serialize(chunk))
            batch.commit()
        return record

    def list_chunks(self, generation_id: str) -> list[dict]:
        prefix = KeyCodec.encode_bytes(generation_id)
        return [
            deserialize(value)
            for _, value in self.db.iter_prefix(ColumnFamily.GENERATION_CHUNKS, prefix)
        ]

    def _list_shards(self, generation_id: str) -> list[dict]:
        prefix = KeyCodec.encode_bytes(generation_id)
        return [
            deserialize(value)
            for _, value in self.db.iter_prefix(ColumnFamily.SHARDS, prefix)
        ]

    def update_status(self, generation_id: str, new_status: str) -> dict:
        generation = self.get(generation_id)
        if generation is None:
            raise KeyError(f"Generation not found: {generation_id}")

        old_status = generation["status"]
        if new_status not in self.TRANSITIONS.get(old_status, set()):
            raise ValueError(
                f"Invalid generation transition: {old_status} -> {new_status}"
            )

        records = self.list_chunks(generation_id)

        if new_status == "SEALING":
            if not records:
                raise ValueError("Cannot seal an empty generation")
            if len(records) != generation["record_count"]:
                raise ValueError("Generation record_count does not match record index")
            indexes = [item["record_index"] for item in records]
            if sorted(indexes) != list(range(generation["record_count"])):
                raise ValueError("Generation record indexes are not contiguous")

        if new_status == "STORED":
            if not records:
                raise ValueError("Cannot store an empty generation")
            shards = self._list_shards(generation_id)
            data_shards = generation["data_shards"]
            total_shards = generation["total_shards"]
            indexes = [s["shard_index"] for s in shards]
            if len(indexes) != len(set(indexes)):
                raise ValueError("Shard indexes must be unique")
            if any(
                not isinstance(i, int)
                or isinstance(i, bool)
                or not 0 <= i < total_shards
                for i in indexes
            ):
                raise ValueError("Shard index is outside configured range")
            if len(indexes) < data_shards:
                raise ValueError("Not enough distinct shards to reconstruct generation")
            for shard in shards:
                if not all(
                    shard.get(field) not in (None, "") for field in ("path", "checksum")
                ):
                    raise ValueError("Shard path and checksum must be present")
                self._require_non_negative_int(shard.get("size"), "shard size")
            for record in records:
                chunk = self._get(
                    ColumnFamily.CHUNKS, KeyCodec.encode_bytes(record["chunk_id"])
                )
                if chunk is None or chunk.get("status") != "ASSIGNED":
                    raise ValueError(
                        f"Chunk is not assigned to generation: {record['chunk_id']}"
                    )
                if chunk.get("generation_id") != generation_id:
                    raise ValueError("Chunk generation assignment mismatch")

        generation["status"] = new_status

        with self.db.batch() as batch:
            batch.put(
                ColumnFamily.GENERATIONS,
                KeyCodec.generation_key(generation_id),
                serialize(generation),
            )

            if new_status == "AVAILABLE":
                for record in records:
                    chunk_key = KeyCodec.encode_bytes(record["chunk_id"])
                    chunk = self._get(ColumnFamily.CHUNKS, chunk_key)
                    if chunk is None:
                        raise KeyError(f"Chunk not found: {record['chunk_id']}")
                    if (
                        chunk.get("status") != "ASSIGNED"
                        or chunk.get("generation_id") != generation_id
                    ):
                        raise ValueError(
                            f"Chunk assignment mismatch: {record['chunk_id']}"
                        )
                    chunk["status"] = "AVAILABLE"
                    batch.put(ColumnFamily.CHUNKS, chunk_key, serialize(chunk))

            batch.commit()
        return generation
