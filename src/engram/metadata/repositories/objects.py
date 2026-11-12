from ..column_families import ColumnFamily
from ..key_codec import KeyCodec
from .base import BaseRepository


class ObjectRepository(BaseRepository):
    def create(self, object_id: str, metadata: dict | None = None) -> dict:
        self._require_non_empty_string(object_id, "object_id")

        key = KeyCodec.object_key(object_id)

        if self.db.get(ColumnFamily.OBJECTS, key) is not None:
            raise ValueError(f"Object already exists: {object_id}")

        record = {
            "object_id": object_id,
            "status": "ACTIVE",
            "latest_version": 0,
            **(metadata or {}),
        }

        if record["object_id"] != object_id:
            raise ValueError("metadata cannot override object_id")

        self._put(ColumnFamily.OBJECTS, key, record)
        return record

    def get(self, object_id: str) -> dict | None:
        return self._get(
            ColumnFamily.OBJECTS,
            KeyCodec.object_key(object_id),
        )

    def update(self, object_id: str, changes: dict) -> dict:
        record = self.get(object_id)

        if record is None:
            raise KeyError(f"Object not found: {object_id}")

        protected = {"object_id"}
        if protected.intersection(changes):
            raise ValueError("Cannot modify object_id")

        record.update(changes)
        self._put(
            ColumnFamily.OBJECTS,
            KeyCodec.object_key(object_id),
            record,
        )
        return record

    def delete(self, object_id: str) -> None:
        key = KeyCodec.object_key(object_id)

        if self.db.get(ColumnFamily.OBJECTS, key) is None:
            raise KeyError(f"Object not found: {object_id}")

        # Không cascade delete version/chunk ở repository này.
        prefix = KeyCodec.encode_bytes(object_id)
        if (
            next(
                self.db.iter_prefix(ColumnFamily.VERSIONS, prefix),
                None,
            )
            is not None
        ):
            raise ValueError("Cannot delete object while versions still exist")

        self._delete(ColumnFamily.OBJECTS, key)
