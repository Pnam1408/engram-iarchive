from ..column_families import ColumnFamily
from ..key_codec import KeyCodec
from collections import Counter

from ..serialization import serialize, deserialize
from .base import BaseRepository


class VersionRepository(BaseRepository):
    PROTECTED_FIELDS = {"object_id", "version", "chunk_ids", "chunk_count"}

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
        extra = self._require_metadata(metadata, self.PROTECTED_FIELDS)

        manifest_key = KeyCodec.version_key(object_id, version)
        if self.db.get(ColumnFamily.VERSIONS, manifest_key) is not None:
            raise ValueError(f"Version already exists: {object_id}@{version}")

        object_key = KeyCodec.object_key(object_id)
        object_record = self._get(ColumnFamily.OBJECTS, object_key)
        if object_record is None:
            raise KeyError(f"Object not found: {object_id}")
        if object_record.get("status") != "ACTIVE":
            raise ValueError(f"Object is not ACTIVE: {object_id}")

        latest_version = object_record.get("latest_version")
        int_lastest_version = self._require_non_negative_int(
            latest_version, "latest_version"
        )
        expected_version = int_lastest_version + 1
        if version != expected_version:
            raise ValueError(f"Expected version {expected_version}, got {version}")

        for chunk_id in chunk_ids:
            chunk = self._get(ColumnFamily.CHUNKS, KeyCodec.encode_bytes(chunk_id))
            if chunk is None:
                raise KeyError(f"Chunk not found: {chunk_id}")
            if chunk.get("object_id") != object_id:
                raise ValueError(f"Chunk {chunk_id} belongs to another object")
            if chunk.get("status") != "AVAILABLE":
                raise ValueError(f"Chunk {chunk_id} is not AVAILABLE")

        manifest = {
            **extra,
            "object_id": object_id,
            "version": version,
            "chunk_ids": list(chunk_ids),
            "chunk_count": len(chunk_ids),
        }

        # Single-writer contract: read/validate/update sequences are not
        # serializable across multiple processes or independent DB handles.
        with self.db.batch() as batch:
            object_record["latest_version"] = version
            batch.put(ColumnFamily.OBJECTS, object_key, serialize(object_record))
            batch.put(ColumnFamily.VERSIONS, manifest_key, serialize(manifest))

            # Preserve order in the manifest index. Reference count counts
            # occurrences, so repeated chunks in one version count repeatedly.
            for index, chunk_id in enumerate(chunk_ids):
                batch.put(
                    ColumnFamily.VERSION_CHUNKS,
                    KeyCodec.version_chunk_key(object_id, version, index),
                    serialize({"chunk_id": chunk_id}),
                )

            for chunk_id, occurrences in Counter(chunk_ids).items():
                ref_key = KeyCodec.encode_bytes(chunk_id)
                raw_ref = self.db.get(ColumnFamily.CHUNK_REFS, ref_key)
                current_ref = (
                    0 if raw_ref is None else deserialize(raw_ref)["reference_count"]
                )
                self._require_non_negative_int(current_ref, "reference_count")
                batch.put(
                    ColumnFamily.CHUNK_REFS,
                    ref_key,
                    serialize(
                        {
                            "chunk_id": chunk_id,
                            "reference_count": current_ref + occurrences,
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
        manifest = self.get(object_id, version)
        if manifest is None:
            raise KeyError(f"Version not found: {object_id}@{version}")
        prefix = KeyCodec.version_chunks_prefix(object_id, version)
        result = [
            deserialize(raw)["chunk_id"]
            for _, raw in self.db.iter_prefix(ColumnFamily.VERSION_CHUNKS, prefix)
        ]
        if result != manifest["chunk_ids"]:
            raise ValueError(
                f"Version chunk index is inconsistent: {object_id}@{version}"
            )
        return result

    def list_versions(self, object_id: str) -> list[int]:
        prefix = KeyCodec.encode_bytes(object_id)
        result = []
        for key, _ in self.db.iter_prefix(ColumnFamily.VERSIONS, prefix):
            decoded_object, offset = KeyCodec.decode_bytes(key)
            if decoded_object.decode("utf-8") != object_id:
                continue
            version, end = KeyCodec.decode_uint64(key, offset)
            if end == len(key):
                result.append(version)
        return sorted(result)
