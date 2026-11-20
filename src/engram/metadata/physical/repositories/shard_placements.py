from ..models import PlacementStatus, ShardPlacement
from ..models._validation import validate_uuid
from ...database import ColumnFamily, KeyCodec
from ...database.serialization import deserialize
from ...repositories import (
    BaseRepository,
    CorruptMetadataError,
    RecordAlreadyExistsError,
)


class ShardPlacementRepository(BaseRepository):
    """Shard location records with a Generation/Shard lookup index."""

    _FIELDS = {
        "placement_id",
        "generation_id",
        "shard_index",
        "storage_node_id",
        "storage_locator",
        "status",
        "created_at",
        "updated_at",
        "stored_at",
        "verified_at",
    }
    _IMMUTABLE_FIELDS = (
        "placement_id",
        "generation_id",
        "shard_index",
        "storage_node_id",
        "storage_locator",
        "created_at",
    )

    @staticmethod
    def _to_dict(placement: ShardPlacement) -> dict:
        return {
            "placement_id": placement.placement_id,
            "generation_id": placement.generation_id,
            "shard_index": placement.shard_index,
            "storage_node_id": placement.storage_node_id,
            "storage_locator": placement.storage_locator,
            "status": placement.status.value,
            "created_at": placement.created_at,
            "updated_at": placement.updated_at,
            "stored_at": placement.stored_at,
            "verified_at": placement.verified_at,
        }

    @classmethod
    def _from_dict(cls, data: dict) -> ShardPlacement:
        cls._require_fields(data, cls._FIELDS, "ShardPlacement")
        values = dict(data)
        values["status"] = PlacementStatus(values["status"])
        return ShardPlacement(**values)

    def _decode_primary(self, raw: bytes, placement_id: str) -> ShardPlacement:
        try:
            data = self._require_dict(deserialize(raw), "ShardPlacement")
            placement = self._from_dict(data)
            if placement.placement_id != placement_id:
                raise CorruptMetadataError(
                    "ShardPlacement ID does not match storage key"
                )
            return placement
        except CorruptMetadataError:
            raise
        except Exception as exc:
            raise CorruptMetadataError("Cannot decode stored ShardPlacement") from exc

    def get(self, placement_id: str) -> ShardPlacement | None:
        self._require_non_empty_string(placement_id, "placement_id")
        raw = self.db.get(
            ColumnFamily.SHARD_PLACEMENTS,
            KeyCodec.shard_placement_key(placement_id),
        )
        return None if raw is None else self._decode_primary(raw, placement_id)

    def list_for_shard(
        self,
        generation_id: str,
        shard_index: int,
    ) -> tuple[ShardPlacement, ...]:
        self._require_non_empty_string(generation_id, "generation_id")
        self._require_non_negative_int(shard_index, "shard_index")
        validate_uuid(generation_id, "generation_id")
        prefix = KeyCodec.shard_placement_index_prefix(generation_id, shard_index)
        placements = []
        for key, raw_index in self.db.iter_prefix(
            ColumnFamily.SHARD_PLACEMENT_INDEX, prefix
        ):
            try:
                if not key.startswith(prefix):
                    raise CorruptMetadataError("ShardPlacement index prefix mismatch")
                placement_id, end = KeyCodec.decode_string(key, len(prefix))
                if end != len(key):
                    raise CorruptMetadataError("Invalid ShardPlacement index key")
                index_data = self._require_dict(
                    deserialize(raw_index), "ShardPlacement index"
                )
                self._require_fields(
                    index_data, {"placement_id"}, "ShardPlacement index"
                )
                if index_data["placement_id"] != placement_id:
                    raise CorruptMetadataError(
                        "ShardPlacement index value does not match its key"
                    )
                placement = self.get(placement_id)
                if placement is None:
                    raise CorruptMetadataError(
                        "ShardPlacement index references a missing primary record"
                    )
                if (
                    placement.generation_id != generation_id
                    or placement.shard_index != shard_index
                    or KeyCodec.shard_placement_index_key(
                        generation_id, shard_index, placement_id
                    )
                    != key
                ):
                    raise CorruptMetadataError(
                        "ShardPlacement index does not match primary record"
                    )
                placements.append(placement)
            except CorruptMetadataError:
                raise
            except Exception as exc:
                raise CorruptMetadataError(
                    "Cannot decode ShardPlacement secondary index"
                ) from exc
        placements.sort(key=lambda item: item.placement_id)
        return tuple(placements)

    def put(self, placement: ShardPlacement, batch) -> None:
        if not isinstance(placement, ShardPlacement):
            raise TypeError("placement must be a ShardPlacement")
        if batch is None:
            raise TypeError("batch is required")

        primary_key = KeyCodec.shard_placement_key(placement.placement_id)
        index_key = KeyCodec.shard_placement_index_key(
            placement.generation_id, placement.shard_index, placement.placement_id
        )
        if self.db.get(ColumnFamily.SHARD_PLACEMENTS, primary_key) is not None:
            raise RecordAlreadyExistsError(
                f"ShardPlacement {placement.placement_id!r} already exists"
            )
        if self.db.get(ColumnFamily.SHARD_PLACEMENT_INDEX, index_key) is not None:
            raise CorruptMetadataError(
                "ShardPlacement secondary index exists without its primary record"
            )

        self._put_batch(
            batch,
            ColumnFamily.SHARD_PLACEMENTS,
            primary_key,
            self._to_dict(placement),
        )
        self._put_batch(
            batch,
            ColumnFamily.SHARD_PLACEMENT_INDEX,
            index_key,
            {"placement_id": placement.placement_id},
        )

    def update(self, placement: ShardPlacement, batch) -> None:
        if not isinstance(placement, ShardPlacement):
            raise TypeError("placement must be a ShardPlacement")
        if batch is None:
            raise TypeError("batch is required")

        existing = self.get(placement.placement_id)
        if existing is None:
            raise KeyError(f"ShardPlacement {placement.placement_id!r} does not exist")

        for field in self._IMMUTABLE_FIELDS:
            if getattr(existing, field) != getattr(placement, field):
                raise ValueError(
                    f"ShardPlacement {field} is immutable and cannot be changed"
                )
        if placement.updated_at < existing.updated_at:
            raise ValueError("ShardPlacement updated_at must not move backwards")
        for field in ("stored_at", "verified_at"):
            old_value = getattr(existing, field)
            new_value = getattr(placement, field)
            if old_value is not None and new_value != old_value:
                raise ValueError(f"ShardPlacement {field} cannot be changed once set")

        index_key = KeyCodec.shard_placement_index_key(
            existing.generation_id,
            existing.shard_index,
            existing.placement_id,
        )
        if self.db.get(ColumnFamily.SHARD_PLACEMENT_INDEX, index_key) is None:
            raise CorruptMetadataError("ShardPlacement secondary index is missing")

        self._put_batch(
            batch,
            ColumnFamily.SHARD_PLACEMENTS,
            KeyCodec.shard_placement_key(placement.placement_id),
            self._to_dict(placement),
        )
