from ..column_families import ColumnFamily
from ..key_codec import KeyCodec
from ..serialization import serialize
from .base import BaseRepository


class ShardRepository(BaseRepository):
    PROTECTED_FIELDS = {"generation_id", "shard_index"}

    def put(self, generation_id: str, shard_index: int, metadata: dict) -> dict:
        self._require_non_empty_string(generation_id, "generation_id")
        self._require_non_negative_int(shard_index, "shard_index")
        extra = self._require_metadata(metadata, self.PROTECTED_FIELDS)

        generation = self._get(
            ColumnFamily.GENERATIONS,
            KeyCodec.generation_key(generation_id),
        )
        if generation is None:
            raise KeyError(f"Generation not found: {generation_id}")
        if generation["status"] != "SEALING":
            raise ValueError(
                "Shards can only be registered while generation is SEALING"
            )
        if shard_index >= generation["total_shards"]:
            raise ValueError("shard_index is outside configured range")

        required = {"path", "size", "checksum"}
        missing = required - extra.keys()
        if missing:
            raise ValueError(f"Missing shard metadata fields: {sorted(missing)}")
        self._require_non_empty_string(extra["path"], "path")
        self._require_non_empty_string(extra["checksum"], "checksum")
        self._require_non_negative_int(extra["size"], "size")
        if extra["size"] == 0:
            raise ValueError("shard size must be greater than zero")

        record = {
            **extra,
            "generation_id": generation_id,
            "shard_index": shard_index,
        }
        key = KeyCodec.shard_key(generation_id, shard_index)
        existing = self._get(ColumnFamily.SHARDS, key)
        if existing is not None:
            if existing == record:
                return existing
            raise ValueError(
                f"Shard already registered: {generation_id}[{shard_index}]"
            )

        self._put(ColumnFamily.SHARDS, key, record)
        return record

    def get(self, generation_id: str, shard_index: int) -> dict | None:
        return self._get(
            ColumnFamily.SHARDS,
            KeyCodec.shard_key(generation_id, shard_index),
        )

    def list_by_generation(self, generation_id: str) -> list[dict]:
        prefix = KeyCodec.encode_bytes(generation_id)
        return [
            self._get_record(value)
            for _, value in self.db.iter_prefix(ColumnFamily.SHARDS, prefix)
        ]

    @staticmethod
    def _get_record(value: bytes) -> dict:
        from ..serialization import deserialize

        return deserialize(value)
