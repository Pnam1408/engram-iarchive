from ..models import GenerationShardCommitment
from ...database import ColumnFamily, KeyCodec
from ...database.serialization import decode_bytes, deserialize, encode_bytes
from ...repositories import (
    BaseRepository,
    CorruptMetadataError,
    RecordAlreadyExistsError,
)


class GenerationShardCommitmentRepository(BaseRepository):
    _FIELDS = {"generation_id", "protocol", "shard_count", "global_root", "created_at"}

    @staticmethod
    def _to_dict(record: GenerationShardCommitment) -> dict:
        return {
            "generation_id": record.generation_id,
            "protocol": record.protocol,
            "shard_count": record.shard_count,
            "global_root": encode_bytes(record.global_root),
            "created_at": record.created_at,
        }

    @classmethod
    def _from_dict(cls, data: dict) -> GenerationShardCommitment:
        cls._require_fields(data, cls._FIELDS, "GenerationShardCommitment")
        return GenerationShardCommitment(
            generation_id=data["generation_id"],
            protocol=data["protocol"],
            shard_count=data["shard_count"],
            global_root=decode_bytes(data["global_root"], "global_root"),
            created_at=data["created_at"],
        )

    def _decode(self, raw: bytes, generation_id: str) -> GenerationShardCommitment:
        try:
            data = self._require_dict(deserialize(raw), "GenerationShardCommitment")
            record = self._from_dict(data)
            if record.generation_id != generation_id:
                raise CorruptMetadataError(
                    "Commitment generation_id does not match key"
                )
            return record
        except CorruptMetadataError:
            raise
        except Exception as exc:
            raise CorruptMetadataError(
                "Cannot decode GenerationShardCommitment"
            ) from exc

    def get(self, generation_id: str) -> GenerationShardCommitment | None:
        self._require_non_empty_string(generation_id, "generation_id")
        key = KeyCodec.generation_shard_commitment_key(generation_id)
        raw = self.db.get(ColumnFamily.GENERATION_SHARD_COMMITMENTS, key)
        return None if raw is None else self._decode(raw, generation_id)

    def exists(self, generation_id: str) -> bool:
        return self.get(generation_id) is not None

    def put(self, record: GenerationShardCommitment, batch) -> None:
        if not isinstance(record, GenerationShardCommitment):
            raise TypeError("record must be GenerationShardCommitment")
        if batch is None:
            raise TypeError("batch is required")
        key = KeyCodec.generation_shard_commitment_key(record.generation_id)
        if self.db.get(ColumnFamily.GENERATION_SHARD_COMMITMENTS, key) is not None:
            raise RecordAlreadyExistsError("Shard commitment already exists")
        self._put_batch(
            batch, ColumnFamily.GENERATION_SHARD_COMMITMENTS, key, self._to_dict(record)
        )
