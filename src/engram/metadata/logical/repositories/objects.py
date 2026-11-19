from ..models import Object
from ...database import ColumnFamily, KeyCodec
from ...repositories import BaseRepository, CorruptMetadataError


class ObjectRepository(BaseRepository):
    """Persistence adapter for mutable Logical Object records."""

    _FIELDS = {
        "object_id",
        "head_sequence",
        "head_version_id",
        "created_at",
        "updated_at",
    }

    def get(self, object_id: str) -> Object | None:
        self._require_non_empty_string(object_id, "object_id")
        try:
            data = self._get(ColumnFamily.OBJECTS, KeyCodec.object_key(object_id))
            if data is None:
                return None
            self._require_fields(data, self._FIELDS, "Object")
            obj = Object(**data)
            if obj.object_id != object_id:
                raise CorruptMetadataError(
                    "ObjectID in value does not match storage key"
                )
            return obj
        except CorruptMetadataError:
            raise
        except Exception as exc:
            raise CorruptMetadataError(f"Cannot decode Object {object_id!r}") from exc

    def exists(self, object_id: str) -> bool:
        self._require_non_empty_string(object_id, "object_id")
        return (
            self.db.get(ColumnFamily.OBJECTS, KeyCodec.object_key(object_id))
            is not None
        )

    def put(self, obj: Object, batch) -> None:
        if not isinstance(obj, Object):
            raise TypeError("obj must be an Object")
        self._put_batch(
            batch,
            ColumnFamily.OBJECTS,
            KeyCodec.object_key(obj.object_id),
            {
                "object_id": obj.object_id,
                "head_sequence": obj.head_sequence,
                "head_version_id": obj.head_version_id,
                "created_at": obj.created_at,
                "updated_at": obj.updated_at,
            },
        )
