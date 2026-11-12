from engram.metadata import batch

from ..column_families import ColumnFamily
from ..key_codec import KeyCodec
from ..serialization import serialize, deserialize
from .base import BaseRepository


class VersionRepository(BaseRepository):
    def create(
        self,
        object_id: str,
        version: int,
        chunk_ids: list[str],
        metadata: dict | None = None,
    ) -> dict:
        self._require_non_empty_string(object_id, "object_id")
        self._require_non_negative_int(version, "version")

        if version == 0:
            raise ValueError("version must be greater than zero")

        if not isinstance(chunk_ids, list):
            raise TypeError("chunk_ids must be a list")

        for chunk_id in chunk_ids:
            self._require_non_empty_string(chunk_id, "chunk_id")

        manifest_key = KeyCodec.version_key(object_id, version)

        if self.db.get(ColumnFamily.VERSIONS, manifest_key) is not None:
            raise ValueError(f"Version already exists: {object_id}@{version}")

        object_key = KeyCodec.object_key(object_id)
        object_record = self._get(ColumnFamily.OBJECTS, object_key)

        if object_record is None:
            raise KeyError(f"Object not found: {object_id}")

        if object_record.get("status") != "ACTIVE":
            raise ValueError(f"Object is not ACTIVE: {object_id}")

        # Version đầu tiên phải là 1; các version tiếp theo tăng tuần tự.
        expected_version = object_record["latest_version"] + 1

        if version != expected_version:
            raise ValueError(f"Expected version {expected_version}, got {version}")

        # Kiểm tra tất cả chunk đã tồn tại và thuộc đúng object.
        chunk_records = []
        for chunk_id in chunk_ids:
            chunk_key = KeyCodec.encode_bytes(chunk_id)
            chunk_record = self._get(ColumnFamily.CHUNKS, chunk_key)

            if chunk_record is None:
                raise KeyError(f"Chunk not found: {chunk_id}")

            if chunk_record.get("object_id") != object_id:
                raise ValueError(f"Chunk {chunk_id} belongs to another object")

            if chunk_record.get("status") != "AVAILABLE":
                raise ValueError(f"Chunk {chunk_id} is not AVAILABLE")

            chunk_records.append(chunk_record)

        manifest = {
            "object_id": object_id,
            "version": version,
            "chunk_ids": chunk_ids,
            "chunk_count": len(chunk_ids),
            **(metadata or {}),
        }

        if (
            manifest["object_id"] != object_id
            or manifest["version"] != version
            or manifest["chunk_ids"] != chunk_ids
        ):
            raise ValueError("metadata cannot override manifest fields")

        # Cập nhật manifest, index và reference counts nguyên tử.
        with self.db.batch() as batch:
            object_record["latest_version"] = version

            batch.put(
                ColumnFamily.OBJECTS,
                object_key,
                serialize(object_record),
            )

            batch.put(
                ColumnFamily.VERSIONS,
                manifest_key,
                serialize(manifest),
            )

            for index, chunk_id in enumerate(chunk_ids):
                index_key = KeyCodec.version_chunk_key(
                    object_id,
                    version,
                    index,
                )
                batch.put(
                    ColumnFamily.VERSION_CHUNKS,
                    index_key,
                    serialize({"chunk_id": chunk_id}),
                )

                ref_key = KeyCodec.encode_bytes(chunk_id)
                raw_ref = self.db.get(ColumnFamily.CHUNK_REFS, ref_key)
                current_ref = (
                    deserialize(raw_ref)["reference_count"]
                    if raw_ref is not None
                    else 0
                )

                batch.put(
                    ColumnFamily.CHUNK_REFS,
                    ref_key,
                    serialize(
                        {
                            "chunk_id": chunk_id,
                            "reference_count": current_ref + 1,
                        }
                    ),
                )

            batch.commit()

        return manifest

    def get(self, object_id: str, version: int) -> dict | None:
        return self._get(
            ColumnFamily.VERSIONS,
            KeyCodec.version_key(object_id, version),
        )

    def get_chunks(self, object_id: str, version: int) -> list[str]:
        if self.get(object_id, version) is None:
            raise KeyError(f"Version not found: {object_id}@{version}")

        prefix = KeyCodec.version_chunks_prefix(object_id, version)
        result = []

        for _, raw in self.db.iter_prefix(
            ColumnFamily.VERSION_CHUNKS,
            prefix,
        ):
            result.append(deserialize(raw)["chunk_id"])

        return result

    def list_versions(self, object_id: str) -> list[int]:
        prefix = KeyCodec.encode_bytes(object_id)
        result = []

        for key, _ in self.db.iter_prefix(
            ColumnFamily.VERSIONS,
            prefix,
        ):
            decoded_object, offset = KeyCodec.decode_bytes(key)

            if decoded_object.decode("utf-8") != object_id:
                continue

            version, end = KeyCodec.decode_uint64(key, offset)

            if end == len(key):
                result.append(version)

        return sorted(result)
