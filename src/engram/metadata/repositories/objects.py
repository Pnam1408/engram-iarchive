from ..column_families import ColumnFamily
from ..key_codec import KeyCodec
from .base import BaseRepository


class ObjectRepository(BaseRepository):
    PROTECTED_FIELDS = {"object_id", "status", "latest_version"}

    def create(self, object_id: str, metadata: dict | None = None) -> dict:
        self._require_non_empty_string(object_id, "object_id")
        extra = self._require_metadata(metadata, self.PROTECTED_FIELDS)
        key = KeyCodec.object_key(object_id)

        if self.db.get(ColumnFamily.OBJECTS, key) is not None:
            raise ValueError(f"Object already exists: {object_id}")

        record = {
            **extra,
            "object_id": object_id,
            "status": "ACTIVE",
            "latest_version": 0,
        }
        self._put(ColumnFamily.OBJECTS, key, record)
        return record

    def get(self, object_id: str) -> dict | None:
        return self._get(ColumnFamily.OBJECTS, KeyCodec.object_key(object_id))

    def update(self, object_id: str, changes: dict) -> dict:
        if not isinstance(changes, dict):
            raise TypeError("changes must be a dict")
        if self.PROTECTED_FIELDS.intersection(changes):
            raise ValueError("Cannot modify object identity or lifecycle fields")

        record = self.get(object_id)
        if record is None:
            raise KeyError(f"Object not found: {object_id}")

        record.update(changes)
        self._put(ColumnFamily.OBJECTS, KeyCodec.object_key(object_id), record)
        return record

    def set_status(self, object_id: str, new_status: str) -> dict:
        allowed = {"ACTIVE": {"DELETING"}, "DELETING": set()}
        record = self.get(object_id)
        if record is None:
            raise KeyError(f"Object not found: {object_id}")
        if new_status not in allowed.get(record["status"], set()):
            raise ValueError(
                f"Invalid object transition: {record['status']} -> {new_status}"
            )
        record["status"] = new_status
        self._put(ColumnFamily.OBJECTS, KeyCodec.object_key(object_id), record)
        return record

    def delete(self, object_id: str) -> None:
        key = KeyCodec.object_key(object_id)
        record = self.get(object_id)
        if record is None:
            raise KeyError(f"Object not found: {object_id}")

        prefix = KeyCodec.encode_bytes(object_id)
        if next(self.db.iter_prefix(ColumnFamily.VERSIONS, prefix), None) is not None:
            raise ValueError("Cannot delete object while versions still exist")

        # Do not leave orphan chunks behind. Physical garbage collection is
        # deliberately outside this repository's responsibility.
        for _, raw in self.db.iter_prefix(ColumnFamily.CHUNKS, b""):
            from ..serialization import deserialize

            chunk = deserialize(raw)
            if chunk.get("object_id") == object_id:
                raise ValueError("Cannot delete object while chunks still exist")

        self._delete(ColumnFamily.OBJECTS, key)
