from ..models import GenerationShard
from ...database import ColumnFamily, KeyCodec
from ...database.serialization import decode_bytes, deserialize, encode_bytes
from ...repositories import (
    BaseRepository,
    CorruptMetadataError,
    RecordAlreadyExistsError,
)


class GenerationShardRepository(BaseRepository):
    """Immutable erasure-coded shard metadata keyed by Generation and index."""

    _FIELDS = {
        "generation_id",
        "shard_index",
        "shard_size",
        "shard_hash",
    }

    @staticmethod
    def _to_dict(shard: GenerationShard) -> dict:
        return {
            "generation_id": shard.generation_id,
            "shard_index": shard.shard_index,
            "shard_size": shard.shard_size,
            "shard_hash": encode_bytes(shard.shard_hash),
        }

    @classmethod
    def _from_dict(cls, data: dict) -> GenerationShard:
        cls._require_fields(data, cls._FIELDS, "GenerationShard")
        return GenerationShard(
            generation_id=data["generation_id"],
            shard_index=data["shard_index"],
            shard_size=data["shard_size"],
            shard_hash=decode_bytes(data["shard_hash"], "shard_hash"),
        )

    @staticmethod
    def _index_from_key(key: bytes, prefix: bytes) -> int:
        if not key.startswith(prefix) or len(key) != len(prefix) + 8:
            raise CorruptMetadataError("Invalid GenerationShard storage key")
        try:
            index, end = KeyCodec.decode_uint64(key, len(prefix))
            if end != len(key):
                raise CorruptMetadataError("Invalid GenerationShard storage key length")
            return index
        except CorruptMetadataError:
            raise
        except Exception as exc:
            raise CorruptMetadataError(
                "Cannot decode GenerationShard storage key"
            ) from exc

    def _decode(
        self,
        raw: bytes,
        generation_id: str,
        shard_index: int,
    ) -> GenerationShard:
        try:
            data = self._require_dict(deserialize(raw), "GenerationShard")
            shard = self._from_dict(data)
            if shard.generation_id != generation_id or shard.shard_index != shard_index:
                raise CorruptMetadataError(
                    "GenerationShard fields do not match storage key"
                )
            return shard
        except CorruptMetadataError:
            raise
        except Exception as exc:
            raise CorruptMetadataError("Cannot decode stored GenerationShard") from exc

    def get(
        self,
        generation_id: str,
        shard_index: int,
    ) -> GenerationShard | None:
        self._require_non_empty_string(generation_id, "generation_id")
        self._require_non_negative_int(shard_index, "shard_index")
        key = KeyCodec.generation_shard_key(generation_id, shard_index)
        raw = self.db.get(ColumnFamily.GENERATION_SHARDS, key)
        return None if raw is None else self._decode(raw, generation_id, shard_index)

    def list_for_generation(
        self,
        generation_id: str,
    ) -> tuple[GenerationShard, ...]:
        self._require_non_empty_string(generation_id, "generation_id")
        prefix = KeyCodec.generation_shard_prefix(generation_id)
        shards = []
        for key, raw in self.db.iter_prefix(ColumnFamily.GENERATION_SHARDS, prefix):
            shard_index = self._index_from_key(key, prefix)
            shards.append(self._decode(raw, generation_id, shard_index))
        if any(a.shard_index >= b.shard_index for a, b in zip(shards, shards[1:])):
            raise CorruptMetadataError("GenerationShards are not strictly ordered")
        return tuple(shards)

    def put(self, shard: GenerationShard, batch) -> None:
        if not isinstance(shard, GenerationShard):
            raise TypeError("shard must be a GenerationShard")
        if batch is None:
            raise TypeError("batch is required")
        key = KeyCodec.generation_shard_key(shard.generation_id, shard.shard_index)
        if self.db.get(ColumnFamily.GENERATION_SHARDS, key) is not None:
            raise RecordAlreadyExistsError(
                f"GenerationShard {shard.generation_id!r}/{shard.shard_index} already exists"
            )
        self._put_batch(
            batch,
            ColumnFamily.GENERATION_SHARDS,
            key,
            self._to_dict(shard),
        )
