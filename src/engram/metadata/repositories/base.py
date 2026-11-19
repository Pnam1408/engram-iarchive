from typing import Any

from ..database import MetadataDB
from ..database.serialization import serialize, deserialize


class BaseRepository:
    """Shared persistence helpers for logical and physical repositories."""

    def __init__(self, db: MetadataDB):
        if db is None:
            raise TypeError("db is required")
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

    def _put_batch(self, batch, column_family, key: bytes, value: dict) -> None:
        """Stage a record in a caller-owned MetadataBatch; never commits it."""
        if batch is None:
            raise TypeError("batch is required")
        batch.put(column_family, key, serialize(value))

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

    @staticmethod
    def _require_dict(value: Any, model_name: str) -> dict:
        if not isinstance(value, dict):
            raise ValueError(f"{model_name} record must be a JSON object")
        return value

    @staticmethod
    def _require_fields(value: dict, fields: set[str], model_name: str) -> None:
        missing = fields - value.keys()
        extra = value.keys() - fields
        if missing or extra:
            raise ValueError(
                f"Invalid {model_name} fields: "
                f"missing={sorted(missing)}, extra={sorted(extra)}"
            )
