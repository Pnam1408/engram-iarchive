from typing import Any

from .. import MetadataDB
from ..database.serialization import serialize, deserialize


class BaseRepository:
    def __init__(self, db: MetadataDB):
        self.db = db

    def _get(self, column_family, key: bytes) -> dict | None:
        raw = self.db.get(column_family, key)
        if raw is None:
            return None
        value = deserialize(raw)
        if not isinstance(value, dict):
            raise ValueError("Repository record must be a JSON object")
        return value

    def _put(self, column_family, key: bytes, value: dict) -> None:
        self.db.put(column_family, key, serialize(value))

    def _delete(self, column_family, key: bytes) -> None:
        self.db.delete(column_family, key)

    @staticmethod
    def _require_non_empty_string(value: Any, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string")
        return value

    @staticmethod
    def _require_non_negative_int(value: Any, field: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{field} must be a non-negative integer")
        return value

    @staticmethod
    def _require_metadata(metadata: dict | None, protected: set[str]) -> dict:
        if metadata is None:
            return {}
        if not isinstance(metadata, dict):
            raise TypeError("metadata must be a dict")
        conflicts = protected.intersection(metadata)
        if conflicts:
            raise ValueError(
                f"metadata cannot set protected fields: {sorted(conflicts)}"
            )
        return dict(metadata)
