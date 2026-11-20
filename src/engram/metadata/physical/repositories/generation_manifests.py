from ..models import GenerationManifest
from ...database import ColumnFamily, KeyCodec
from ...database.serialization import decode_bytes, deserialize, encode_bytes
from ...repositories import (
    BaseRepository,
    CorruptMetadataError,
    RecordAlreadyExistsError,
)


class GenerationManifestRepository(BaseRepository):
    """Immutable one-per-Generation manifest repository."""

    _FIELDS = {
        "generation_id",
        "entry_count",
        "payload_size",
        "manifest_root",
        "created_at",
    }

    @staticmethod
    def _to_dict(manifest: GenerationManifest) -> dict:
        return {
            "generation_id": manifest.generation_id,
            "entry_count": manifest.entry_count,
            "payload_size": manifest.payload_size,
            "manifest_root": encode_bytes(manifest.manifest_root),
            "created_at": manifest.created_at,
        }

    @classmethod
    def _from_dict(cls, data: dict) -> GenerationManifest:
        cls._require_fields(data, cls._FIELDS, "GenerationManifest")
        return GenerationManifest(
            generation_id=data["generation_id"],
            entry_count=data["entry_count"],
            payload_size=data["payload_size"],
            manifest_root=decode_bytes(data["manifest_root"], "manifest_root"),
            created_at=data["created_at"],
        )

    def _decode(self, raw: bytes, generation_id: str) -> GenerationManifest:
        try:
            data = self._require_dict(deserialize(raw), "GenerationManifest")
            manifest = self._from_dict(data)
            if manifest.generation_id != generation_id:
                raise CorruptMetadataError(
                    "GenerationManifest fields do not match storage key"
                )
            return manifest
        except CorruptMetadataError:
            raise
        except Exception as exc:
            raise CorruptMetadataError(
                "Cannot decode stored GenerationManifest"
            ) from exc

    def get(self, generation_id: str) -> GenerationManifest | None:
        self._require_non_empty_string(generation_id, "generation_id")
        raw = self.db.get(
            ColumnFamily.GENERATION_MANIFESTS,
            KeyCodec.generation_manifest_key(generation_id),
        )
        return None if raw is None else self._decode(raw, generation_id)

    def put(self, manifest: GenerationManifest, batch) -> None:
        if not isinstance(manifest, GenerationManifest):
            raise TypeError("manifest must be a GenerationManifest")
        if batch is None:
            raise TypeError("batch is required")

        key = KeyCodec.generation_manifest_key(manifest.generation_id)
        if self.db.get(ColumnFamily.GENERATION_MANIFESTS, key) is not None:
            raise RecordAlreadyExistsError(
                f"GenerationManifest for {manifest.generation_id!r} already exists"
            )
        self._put_batch(
            batch,
            ColumnFamily.GENERATION_MANIFESTS,
            key,
            self._to_dict(manifest),
        )
