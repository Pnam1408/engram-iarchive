"""Canonical encoding for Generation Manifest commitments."""

from uuid import UUID

from engram.commitment.common import (
    GENERATION_MANIFEST,
    encode_list,
    encode_raw_bytes,
    encode_string,
    encode_u32,
    encode_u64,
    encode_uuid,
    hash_with_domain,
)
from engram.commitment.common.exceptions import InvalidInputError
from engram.metadata.physical.models import (
    Generation,
    GenerationManifest,
    GenerationManifestEntry,
)


def _uuid_from_model(value: str, field: str) -> UUID:
    if type(value) is not str:
        raise TypeError(f"{field} must be a UUID string in the Metadata model")
    try:
        return UUID(value)
    except (ValueError, AttributeError) as exc:
        raise InvalidInputError(f"{field} must be a valid UUID") from exc


class GenerationCanonicalizer:
    """Canonicalizes the immutable logical contents of a Generation Manifest.

    Lifecycle metadata, timestamps, placements, and the root itself are excluded.
    Record hashes are expected to be SHA-256 digests of complete serialized record
    bytes (header + encrypted content), computed by Processing.
    """

    @staticmethod
    def _encode_entry(entry: GenerationManifestEntry) -> bytes:
        if not isinstance(entry, GenerationManifestEntry):
            raise TypeError("entries must contain GenerationManifestEntry instances")
        if type(entry.record_hash) is not bytes or len(entry.record_hash) != 32:
            raise ValueError("record_hash must be exactly 32 bytes")
        return b"".join(
            (
                encode_uuid(_uuid_from_model(entry.record_id, "record_id")),
                encode_string(entry.object_id),
                encode_uuid(_uuid_from_model(entry.chunk_id, "chunk_id")),
                encode_u64(entry.offset),
                encode_u64(entry.length),
                encode_raw_bytes(entry.record_hash),
            )
        )

    @classmethod
    def encode(
        cls,
        *,
        generation: Generation,
        manifest: GenerationManifest,
        entries: tuple[GenerationManifestEntry, ...],
    ) -> bytes:
        if not isinstance(generation, Generation):
            raise TypeError("generation must be a Generation")
        if not isinstance(manifest, GenerationManifest):
            raise TypeError("manifest must be a GenerationManifest")
        if type(entries) is not tuple:
            raise TypeError("entries must be a tuple")
        if any(not isinstance(entry, GenerationManifestEntry) for entry in entries):
            raise TypeError("entries must contain GenerationManifestEntry instances")
        if generation.generation_id != manifest.generation_id:
            raise InvalidInputError("Generation and Manifest IDs do not match")

        ordered = tuple(sorted(entries, key=lambda entry: entry.offset))
        # Reuse Metadata's structural validation (identity, uniqueness, contiguity,
        # count, and total size) before producing canonical bytes.
        manifest.validate_entries(ordered)

        return b"".join(
            (
                encode_uuid(
                    _uuid_from_model(generation.generation_id, "generation_id")
                ),
                encode_u32(generation.data_shards),
                encode_u32(generation.total_shards),
                encode_u32(manifest.entry_count),
                encode_u64(manifest.payload_size),
                encode_list(ordered, cls._encode_entry),
            )
        )

    @classmethod
    def hash(
        cls,
        *,
        generation: Generation,
        manifest: GenerationManifest,
        entries: tuple[GenerationManifestEntry, ...],
    ) -> bytes:
        return hash_with_domain(
            GENERATION_MANIFEST,
            cls.encode(generation=generation, manifest=manifest, entries=entries),
        )
