from ..models import GenerationManifest, GenerationManifestEntry
from ...database import ColumnFamily, KeyCodec
from ...database.serialization import decode_bytes, deserialize, encode_bytes
from ...repositories import (
    BaseRepository,
    CorruptMetadataError,
    RecordAlreadyExistsError,
)


class GenerationManifestEntryRepository(BaseRepository):
    """Immutable Manifest entries ordered by their payload byte offset."""

    _FIELDS = {
        "generation_id",
        "record_id",
        "object_id",
        "chunk_id",
        "offset",
        "length",
        "record_hash",
    }

    @staticmethod
    def _to_dict(entry: GenerationManifestEntry) -> dict:
        return {
            "generation_id": entry.generation_id,
            "record_id": entry.record_id,
            "object_id": entry.object_id,
            "chunk_id": entry.chunk_id,
            "offset": entry.offset,
            "length": entry.length,
            "record_hash": encode_bytes(entry.record_hash),
        }

    @classmethod
    def _from_dict(cls, data: dict) -> GenerationManifestEntry:
        cls._require_fields(data, cls._FIELDS, "GenerationManifestEntry")
        return GenerationManifestEntry(
            generation_id=data["generation_id"],
            record_id=data["record_id"],
            object_id=data["object_id"],
            chunk_id=data["chunk_id"],
            offset=data["offset"],
            length=data["length"],
            record_hash=decode_bytes(data["record_hash"], "record_hash"),
        )

    @staticmethod
    def _offset_from_key(key: bytes, prefix: bytes) -> int:
        if not key.startswith(prefix) or len(key) != len(prefix) + 8:
            raise CorruptMetadataError("Invalid GenerationManifestEntry storage key")
        try:
            offset, end = KeyCodec.decode_uint64(key, len(prefix))
            if end != len(key):
                raise CorruptMetadataError(
                    "Invalid GenerationManifestEntry storage key length"
                )
            return offset
        except CorruptMetadataError:
            raise
        except Exception as exc:
            raise CorruptMetadataError(
                "Cannot decode GenerationManifestEntry storage key"
            ) from exc

    def _decode(
        self,
        raw: bytes,
        generation_id: str,
        offset: int,
    ) -> GenerationManifestEntry:
        try:
            data = self._require_dict(deserialize(raw), "GenerationManifestEntry")
            entry = self._from_dict(data)
            if entry.generation_id != generation_id or entry.offset != offset:
                raise CorruptMetadataError(
                    "GenerationManifestEntry fields do not match storage key"
                )
            return entry
        except CorruptMetadataError:
            raise
        except Exception as exc:
            raise CorruptMetadataError(
                "Cannot decode stored GenerationManifestEntry"
            ) from exc

    def get(
        self,
        generation_id: str,
        offset: int,
    ) -> GenerationManifestEntry | None:
        self._require_non_empty_string(generation_id, "generation_id")
        self._require_non_negative_int(offset, "offset")
        key = KeyCodec.generation_manifest_entry_key(generation_id, offset)
        raw = self.db.get(ColumnFamily.GENERATION_MANIFEST_ENTRIES, key)
        return None if raw is None else self._decode(raw, generation_id, offset)

    def list_for_generation(
        self,
        generation_id: str,
    ) -> tuple[GenerationManifestEntry, ...]:
        self._require_non_empty_string(generation_id, "generation_id")
        prefix = KeyCodec.generation_manifest_entry_prefix(generation_id)
        entries = []
        for key, raw in self.db.iter_prefix(
            ColumnFamily.GENERATION_MANIFEST_ENTRIES, prefix
        ):
            offset = self._offset_from_key(key, prefix)
            entries.append(self._decode(raw, generation_id, offset))
        result = tuple(entries)
        if len({entry.record_id for entry in result}) != len(result):
            raise CorruptMetadataError(
                "Generation Manifest contains duplicate record_id values"
            )
        if any(a.offset >= b.offset for a, b in zip(result, result[1:])):
            raise CorruptMetadataError(
                "Generation Manifest entries are not strictly ordered"
            )
        return result

    def put_all(
        self,
        manifest: GenerationManifest,
        entries: tuple[GenerationManifestEntry, ...],
        batch,
    ) -> None:
        if not isinstance(manifest, GenerationManifest):
            raise TypeError("manifest must be a GenerationManifest")
        if not isinstance(entries, tuple) or any(
            not isinstance(entry, GenerationManifestEntry) for entry in entries
        ):
            raise TypeError("entries must be a tuple of GenerationManifestEntry")
        if batch is None:
            raise TypeError("batch is required")

        # Domain-level validation checks count, ownership, uniqueness,
        # contiguous offsets, and total payload size.
        ordered = manifest.validate_entries(entries)
        prefix = KeyCodec.generation_manifest_entry_prefix(manifest.generation_id)
        if (
            next(
                self.db.iter_prefix(ColumnFamily.GENERATION_MANIFEST_ENTRIES, prefix),
                None,
            )
            is not None
        ):
            raise RecordAlreadyExistsError(
                f"Manifest entries for Generation {manifest.generation_id!r} already exist"
            )

        for entry in ordered:
            key = KeyCodec.generation_manifest_entry_key(
                manifest.generation_id, entry.offset
            )
            if self.db.get(ColumnFamily.GENERATION_MANIFEST_ENTRIES, key) is not None:
                raise RecordAlreadyExistsError(
                    f"Manifest entry at offset {entry.offset} already exists"
                )

        for entry in ordered:
            self._put_batch(
                batch,
                ColumnFamily.GENERATION_MANIFEST_ENTRIES,
                KeyCodec.generation_manifest_entry_key(
                    manifest.generation_id, entry.offset
                ),
                self._to_dict(entry),
            )
