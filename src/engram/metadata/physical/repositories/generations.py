from ..models import Generation, GenerationStatus
from ...database import ColumnFamily, KeyCodec
from ...database.serialization import deserialize
from ...repositories import BaseRepository, CorruptMetadataError


class GenerationRepository(BaseRepository):
    """Persistence adapter for mutable Generation lifecycle records."""

    _FIELDS = {
        "generation_id",
        "data_shards",
        "total_shards",
        "status",
        "created_at",
        "updated_at",
        "sealed_at",
        "encoded_at",
    }
    _IMMUTABLE_FIELDS = (
        "generation_id",
        "data_shards",
        "total_shards",
        "created_at",
    )

    @staticmethod
    def _to_dict(generation: Generation) -> dict:
        return {
            "generation_id": generation.generation_id,
            "data_shards": generation.data_shards,
            "total_shards": generation.total_shards,
            "status": generation.status.value,
            "created_at": generation.created_at,
            "updated_at": generation.updated_at,
            "sealed_at": generation.sealed_at,
            "encoded_at": generation.encoded_at,
        }

    @classmethod
    def _from_dict(cls, data: dict) -> Generation:
        cls._require_fields(data, cls._FIELDS, "Generation")
        values = dict(data)
        values["status"] = GenerationStatus(values["status"])
        return Generation(**values)

    def _decode(self, raw: bytes, generation_id: str) -> Generation:
        try:
            data = self._require_dict(deserialize(raw), "Generation")
            generation = self._from_dict(data)
            if generation.generation_id != generation_id:
                raise CorruptMetadataError(
                    "GenerationID in value does not match storage key"
                )
            return generation
        except CorruptMetadataError:
            raise
        except Exception as exc:
            raise CorruptMetadataError(
                f"Cannot decode stored Generation {generation_id!r}"
            ) from exc

    def get(self, generation_id: str) -> Generation | None:
        self._require_non_empty_string(generation_id, "generation_id")
        raw = self.db.get(
            ColumnFamily.GENERATIONS,
            KeyCodec.generation_key(generation_id),
        )
        return None if raw is None else self._decode(raw, generation_id)

    def exists(self, generation_id: str) -> bool:
        self._require_non_empty_string(generation_id, "generation_id")
        return (
            self.db.get(
                ColumnFamily.GENERATIONS,
                KeyCodec.generation_key(generation_id),
            )
            is not None
        )

    def put(self, generation: Generation, batch) -> None:
        if not isinstance(generation, Generation):
            raise TypeError("generation must be a Generation")
        if batch is None:
            raise TypeError("batch is required")

        existing = self.get(generation.generation_id)
        if existing is not None:
            for field in self._IMMUTABLE_FIELDS:
                if getattr(existing, field) != getattr(generation, field):
                    raise ValueError(
                        f"Generation {field} is immutable and cannot be changed"
                    )
            if generation.updated_at < existing.updated_at:
                raise ValueError("Generation updated_at must not move backwards")

        self._put_batch(
            batch,
            ColumnFamily.GENERATIONS,
            KeyCodec.generation_key(generation.generation_id),
            self._to_dict(generation),
        )
