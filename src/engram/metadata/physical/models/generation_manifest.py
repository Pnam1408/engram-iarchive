from dataclasses import dataclass
from typing import Iterable

from ._validation import (
    validate_hash,
    validate_positive_integer,
    validate_timestamp,
    validate_uuid,
)
from .generation_manifest_entry import GenerationManifestEntry


@dataclass(frozen=True, slots=True)
class GenerationManifest:
    generation_id: str

    entry_count: int
    payload_size: int

    manifest_root: bytes

    created_at: int

    def __post_init__(self) -> None:
        validate_uuid(self.generation_id, "generation_id")
        validate_positive_integer(self.entry_count, "entry_count")
        validate_positive_integer(self.payload_size, "payload_size")
        validate_hash(self.manifest_root, "manifest_root")
        validate_timestamp(self.created_at, "created_at")

    def validate_entries(
        self, entries: Iterable[GenerationManifestEntry]
    ) -> tuple[GenerationManifestEntry, ...]:
        """Validate the complete, offset-ordered entry set described by this manifest."""
        materialized = tuple(entries)
        if len(materialized) != self.entry_count:
            raise ValueError("Manifest entry count does not match entry_count")

        expected_offset = 0
        record_ids: set[str] = set()
        logical_chunks: set[tuple[str, str]] = set()

        for entry in materialized:
            if not isinstance(entry, GenerationManifestEntry):
                raise TypeError("entries must contain GenerationManifestEntry values")
            if entry.generation_id != self.generation_id:
                raise ValueError("Manifest entry belongs to a different Generation")
            if entry.record_id in record_ids:
                raise ValueError("Manifest contains duplicate record_id values")
            record_ids.add(entry.record_id)

            logical_key = (entry.object_id, entry.chunk_id)
            if logical_key in logical_chunks:
                raise ValueError("Manifest contains duplicate Object/Chunk pairs")
            logical_chunks.add(logical_key)

            if entry.offset != expected_offset:
                raise ValueError(
                    "Manifest entries must be contiguous and ordered by offset"
                )
            expected_offset += entry.length

        if expected_offset != self.payload_size:
            raise ValueError("Manifest payload_size does not match entry lengths")

        return materialized
