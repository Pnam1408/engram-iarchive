import base64
import binascii

from ..models import Version
from ...database import ColumnFamily, KeyCodec
from ...repositories import (
    BaseRepository,
    CorruptMetadataError,
    RecordAlreadyExistsError,
)


class VersionRepository(BaseRepository):
    """Append-only Version persistence keyed by ObjectID + sequence."""

    _FIELDS = {
        "version_id",
        "object_id",
        "sequence",
        "parent_version_id",
        "delta_root",
        "previous_version_hash",
        "codec",
        "writer_public_key",
        "signature",
        "created_at",
    }

    @staticmethod
    def _encode_bytes(value: bytes) -> str:
        return base64.b64encode(value).decode("ascii")

    @staticmethod
    def _decode_bytes(value, field: str) -> bytes:
        if not isinstance(value, str):
            raise ValueError(f"{field} must be a Base64 string")
        try:
            return base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(f"Invalid Base64 value for {field}") from exc

    @classmethod
    def _to_dict(cls, version: Version) -> dict:
        return {
            "version_id": version.version_id,
            "object_id": version.object_id,
            "sequence": version.sequence,
            "parent_version_id": version.parent_version_id,
            "delta_root": cls._encode_bytes(version.delta_root),
            "previous_version_hash": (
                None
                if version.previous_version_hash is None
                else cls._encode_bytes(version.previous_version_hash)
            ),
            "codec": version.codec,
            "writer_public_key": cls._encode_bytes(version.writer_public_key),
            "signature": cls._encode_bytes(version.signature),
            "created_at": version.created_at,
        }

    @classmethod
    def _from_dict(cls, data: dict) -> Version:
        cls._require_fields(data, cls._FIELDS, "Version")
        previous_hash = data["previous_version_hash"]
        return Version(
            version_id=data["version_id"],
            object_id=data["object_id"],
            sequence=data["sequence"],
            parent_version_id=data["parent_version_id"],
            delta_root=cls._decode_bytes(data["delta_root"], "delta_root"),
            previous_version_hash=(
                None
                if previous_hash is None
                else cls._decode_bytes(previous_hash, "previous_version_hash")
            ),
            codec=data["codec"],
            writer_public_key=cls._decode_bytes(
                data["writer_public_key"], "writer_public_key"
            ),
            signature=cls._decode_bytes(data["signature"], "signature"),
            created_at=data["created_at"],
        )

    def get(self, object_id: str, sequence: int) -> Version | None:
        key = KeyCodec.version_key(object_id, sequence)
        try:
            data = self._get(ColumnFamily.VERSIONS, key)
            if data is None:
                return None
            return self._decode_record(data, object_id, sequence)
        except CorruptMetadataError:
            raise
        except Exception as exc:
            raise CorruptMetadataError("Cannot decode stored Version") from exc

    def get_by_id(self, object_id: str, version_id: str) -> Version | None:
        self._require_non_empty_string(object_id, "object_id")
        self._require_non_empty_string(version_id, "version_id")
        prefix = KeyCodec.version_prefix(object_id)
        for key, raw in self.db.iter_prefix(ColumnFamily.VERSIONS, prefix):
            sequence = self._sequence_from_key(key, prefix)
            data = self._require_dict_from_raw(raw, "Version")
            version = self._decode_record(data, object_id, sequence)
            if version.version_id == version_id:
                return version
        return None

    def get_latest(self, object_id: str) -> Version | None:
        from .objects import ObjectRepository

        obj = ObjectRepository(self.db).get(object_id)
        if obj is None or obj.head_sequence == 0:
            return None
        version = self.get(object_id, obj.head_sequence)
        if version is None or version.version_id != obj.head_version_id:
            raise CorruptMetadataError(
                "Object Head does not reference an existing Version"
            )
        return version

    def list_versions(
        self,
        object_id: str,
        *,
        start_sequence: int = 1,
        limit: int | None = None,
    ) -> tuple[Version, ...]:
        self._require_non_empty_string(object_id, "object_id")
        if (
            isinstance(start_sequence, bool)
            or not isinstance(start_sequence, int)
            or start_sequence < 1
        ):
            raise ValueError("start_sequence must be an integer >= 1")
        if limit is not None and (
            isinstance(limit, bool) or not isinstance(limit, int) or limit < 0
        ):
            raise ValueError("limit must be a non-negative integer or None")
        if limit == 0:
            return ()

        versions = []
        prefix = KeyCodec.version_prefix(object_id)
        for key, raw in self.db.iter_prefix(ColumnFamily.VERSIONS, prefix):
            sequence = self._sequence_from_key(key, prefix)
            if sequence < start_sequence:
                continue
            data = self._require_dict_from_raw(raw, "Version")
            versions.append(self._decode_record(data, object_id, sequence))
            if limit is not None and len(versions) >= limit:
                break
        return tuple(versions)

    def put(self, version: Version, batch) -> None:
        if not isinstance(version, Version):
            raise TypeError("version must be a Version")
        key = KeyCodec.version_key(version.object_id, version.sequence)
        if self.db.get(ColumnFamily.VERSIONS, key) is not None:
            raise RecordAlreadyExistsError(
                f"Version {version.object_id!r}/{version.sequence} already exists"
            )
        if self.get_by_id(version.object_id, version.version_id) is not None:
            raise RecordAlreadyExistsError(
                f"VersionID {version.version_id!r} already exists"
            )
        self._put_batch(batch, ColumnFamily.VERSIONS, key, self._to_dict(version))

    @staticmethod
    def _require_dict_from_raw(raw: bytes, model_name: str) -> dict:
        from ...database.serialization import deserialize

        try:
            value = deserialize(raw)
            return BaseRepository._require_dict(value, model_name)
        except Exception as exc:
            if isinstance(exc, CorruptMetadataError):
                raise
            raise CorruptMetadataError(f"Cannot decode stored {model_name}") from exc

    @staticmethod
    def _sequence_from_key(key: bytes, prefix: bytes) -> int:
        try:
            if not key.startswith(prefix) or len(key) != len(prefix) + 8:
                raise CorruptMetadataError("Invalid Version storage key")
            sequence, end = KeyCodec.decode_uint64(key, len(prefix))
            if end != len(key):
                raise CorruptMetadataError("Invalid Version storage key length")
            return sequence
        except CorruptMetadataError:
            raise
        except Exception as exc:
            raise CorruptMetadataError("Cannot decode Version storage key") from exc

    def _decode_record(self, data: dict, object_id: str, sequence: int) -> Version:
        try:
            version = self._from_dict(data)
            if version.object_id != object_id or version.sequence != sequence:
                raise CorruptMetadataError("Version fields do not match storage key")
            return version
        except CorruptMetadataError:
            raise
        except Exception as exc:
            raise CorruptMetadataError("Cannot decode stored Version") from exc
