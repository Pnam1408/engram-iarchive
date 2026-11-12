from ..column_families import ColumnFamily
from ..key_codec import KeyCodec
from .base import BaseRepository


class ShardRepository(BaseRepository):
    def put(
        self,
        generation_id: str,
        shard_index: int,
        metadata: dict,
    ) -> dict:
        self._require_non_empty_string(
            generation_id,
            "generation_id",
        )
        self._require_non_negative_int(shard_index, "shard_index")

        generation = self._get(
            ColumnFamily.GENERATIONS,
            KeyCodec.generation_key(generation_id),
        )

        if generation is None:
            raise KeyError(f"Generation not found: {generation_id}")

        if generation["status"] not in {"SEALING", "STORED"}:
            raise ValueError("Shards can only be registered while SEALING or STORED")

        record = {
            "generation_id": generation_id,
            "shard_index": shard_index,
            **metadata,
        }

        if (
            record["generation_id"] != generation_id
            or record["shard_index"] != shard_index
        ):
            raise ValueError("metadata cannot override shard identity")

        required = {"path", "size", "checksum"}
        missing = required - record.keys()

        if missing:
            raise ValueError(f"Missing shard metadata fields: {sorted(missing)}")

        self._require_non_negative_int(record["size"], "size")

        key = KeyCodec.shard_key(generation_id, shard_index)

        self._put(ColumnFamily.SHARDS, key, record)
        return record

    def get(
        self,
        generation_id: str,
        shard_index: int,
    ) -> dict | None:
        return self._get(
            ColumnFamily.SHARDS,
            KeyCodec.shard_key(generation_id, shard_index),
        )

    def list_by_generation(self, generation_id: str) -> list[dict]:
        prefix = KeyCodec.encode_bytes(generation_id)

        return [
            self._get_record(value)
            for _, value in self.db.iter_prefix(
                ColumnFamily.SHARDS,
                prefix,
            )
        ]

    @staticmethod
    def _get_record(value: bytes) -> dict:
        from ..serialization import deserialize

        return deserialize(value)
