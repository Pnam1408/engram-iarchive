from ..models import ChunkPlacement
from ..models._validation import validate_uuid
from ...database import ColumnFamily, KeyCodec
from ...database.serialization import deserialize
from ...repositories import (
    BaseRepository,
    CorruptMetadataError,
    RecordAlreadyExistsError,
)


class ChunkPlacementRepository(BaseRepository):
    """Immutable Chunk-to-Generation record mappings with a lookup index."""

    _FIELDS = {
        "placement_id",
        "object_id",
        "chunk_id",
        "generation_id",
        "record_id",
        "created_at",
    }

    @staticmethod
    def _to_dict(placement: ChunkPlacement) -> dict:
        return {
            "placement_id": placement.placement_id,
            "object_id": placement.object_id,
            "chunk_id": placement.chunk_id,
            "generation_id": placement.generation_id,
            "record_id": placement.record_id,
            "created_at": placement.created_at,
        }

    @classmethod
    def _from_dict(cls, data: dict) -> ChunkPlacement:
        cls._require_fields(data, cls._FIELDS, "ChunkPlacement")
        return ChunkPlacement(**data)

    def _decode_primary(self, raw: bytes, placement_id: str) -> ChunkPlacement:
        try:
            data = self._require_dict(deserialize(raw), "ChunkPlacement")
            placement = self._from_dict(data)
            if placement.placement_id != placement_id:
                raise CorruptMetadataError(
                    "ChunkPlacement ID does not match storage key"
                )
            return placement
        except CorruptMetadataError:
            raise
        except Exception as exc:
            raise CorruptMetadataError("Cannot decode stored ChunkPlacement") from exc

    def get(self, placement_id: str) -> ChunkPlacement | None:
        self._require_non_empty_string(placement_id, "placement_id")
        raw = self.db.get(
            ColumnFamily.CHUNK_PLACEMENTS,
            KeyCodec.chunk_placement_key(placement_id),
        )
        return None if raw is None else self._decode_primary(raw, placement_id)

    def list_for_chunk(
        self,
        object_id: str,
        chunk_id: str,
    ) -> tuple[ChunkPlacement, ...]:
        self._require_non_empty_string(object_id, "object_id")
        validate_uuid(chunk_id, "chunk_id")
        prefix = KeyCodec.chunk_placement_index_prefix(object_id, chunk_id)
        placements = []
        for key, raw_index in self.db.iter_prefix(
            ColumnFamily.CHUNK_PLACEMENT_INDEX, prefix
        ):
            try:
                if not key.startswith(prefix):
                    raise CorruptMetadataError("ChunkPlacement index prefix mismatch")
                placement_id, end = KeyCodec.decode_string(key, len(prefix))
                if end != len(key):
                    raise CorruptMetadataError("Invalid ChunkPlacement index key")
                index_data = self._require_dict(
                    deserialize(raw_index), "ChunkPlacement index"
                )
                self._require_fields(
                    index_data, {"placement_id"}, "ChunkPlacement index"
                )
                if index_data["placement_id"] != placement_id:
                    raise CorruptMetadataError(
                        "ChunkPlacement index value does not match its key"
                    )
                placement = self.get(placement_id)
                if placement is None:
                    raise CorruptMetadataError(
                        "ChunkPlacement index references a missing primary record"
                    )
                if (
                    placement.object_id != object_id
                    or placement.chunk_id != chunk_id
                    or KeyCodec.chunk_placement_index_key(
                        object_id, chunk_id, placement_id
                    )
                    != key
                ):
                    raise CorruptMetadataError(
                        "ChunkPlacement index does not match primary record"
                    )
                placements.append(placement)
            except CorruptMetadataError:
                raise
            except Exception as exc:
                raise CorruptMetadataError(
                    "Cannot decode ChunkPlacement secondary index"
                ) from exc
        placements.sort(key=lambda item: item.placement_id)
        return tuple(placements)

    def put(self, placement: ChunkPlacement, batch) -> None:
        if not isinstance(placement, ChunkPlacement):
            raise TypeError("placement must be a ChunkPlacement")
        if batch is None:
            raise TypeError("batch is required")

        primary_key = KeyCodec.chunk_placement_key(placement.placement_id)
        index_key = KeyCodec.chunk_placement_index_key(
            placement.object_id, placement.chunk_id, placement.placement_id
        )
        if self.db.get(ColumnFamily.CHUNK_PLACEMENTS, primary_key) is not None:
            raise RecordAlreadyExistsError(
                f"ChunkPlacement {placement.placement_id!r} already exists"
            )
        if self.db.get(ColumnFamily.CHUNK_PLACEMENT_INDEX, index_key) is not None:
            raise CorruptMetadataError(
                "ChunkPlacement secondary index exists without its primary record"
            )

        self._put_batch(
            batch,
            ColumnFamily.CHUNK_PLACEMENTS,
            primary_key,
            self._to_dict(placement),
        )
        self._put_batch(
            batch,
            ColumnFamily.CHUNK_PLACEMENT_INDEX,
            index_key,
            {"placement_id": placement.placement_id},
        )
