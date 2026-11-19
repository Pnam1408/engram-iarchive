from uuid import uuid4

import pytest

from engram.metadata.physical.models import (
    GenerationManifest,
    GenerationManifestEntry,
)


def make_entry(
    generation_id, *, offset=0, length=10, object_id="object-A", chunk_id=None
):
    return GenerationManifestEntry(
        generation_id=generation_id,
        record_id=str(uuid4()),
        object_id=object_id,
        version_id=str(uuid4()),
        chunk_id=chunk_id or str(uuid4()),
        offset=offset,
        length=length,
        record_hash=b"r" * 32,
    )


def make_manifest(generation_id, *, entry_count=1, payload_size=10):
    return GenerationManifest(
        generation_id=generation_id,
        entry_count=entry_count,
        payload_size=payload_size,
        manifest_root=b"m" * 32,
        created_at=100,
    )


def test_manifest_validates_complete_contiguous_entry_set():
    generation_id = str(uuid4())
    entries = (
        make_entry(generation_id, offset=0, length=10),
        make_entry(generation_id, offset=10, length=5),
    )
    manifest = make_manifest(generation_id, entry_count=2, payload_size=15)
    assert manifest.validate_entries(entries) == entries


def test_manifest_rejects_entry_count_mismatch():
    generation_id = str(uuid4())
    manifest = make_manifest(generation_id, entry_count=2, payload_size=10)
    with pytest.raises(ValueError, match="entry count"):
        manifest.validate_entries((make_entry(generation_id),))


def test_manifest_rejects_entry_from_another_generation():
    generation_id = str(uuid4())
    manifest = make_manifest(generation_id)
    with pytest.raises(ValueError, match="different Generation"):
        manifest.validate_entries((make_entry(str(uuid4())),))


def test_manifest_rejects_non_contiguous_or_unordered_offsets():
    generation_id = str(uuid4())
    manifest = make_manifest(generation_id, entry_count=2, payload_size=20)
    entries = (
        make_entry(generation_id, offset=0, length=10),
        make_entry(generation_id, offset=11, length=9),
    )
    with pytest.raises(ValueError, match="contiguous"):
        manifest.validate_entries(entries)


def test_manifest_rejects_duplicate_record_ids():
    generation_id = str(uuid4())
    first = make_entry(generation_id, offset=0, length=5)
    second = GenerationManifestEntry(
        generation_id=generation_id,
        record_id=first.record_id,
        object_id="object-B",
        version_id=str(uuid4()),
        chunk_id=str(uuid4()),
        offset=5,
        length=5,
        record_hash=b"r" * 32,
    )
    manifest = make_manifest(generation_id, entry_count=2, payload_size=10)
    with pytest.raises(ValueError, match="duplicate record_id"):
        manifest.validate_entries((first, second))


def test_manifest_rejects_duplicate_object_chunk_pair():
    generation_id = str(uuid4())
    chunk_id = str(uuid4())
    entries = (
        make_entry(generation_id, offset=0, length=5, chunk_id=chunk_id),
        make_entry(generation_id, offset=5, length=5, chunk_id=chunk_id),
    )
    manifest = make_manifest(generation_id, entry_count=2, payload_size=10)
    with pytest.raises(ValueError, match="duplicate Object/Chunk"):
        manifest.validate_entries(entries)


def test_manifest_rejects_payload_size_mismatch():
    generation_id = str(uuid4())
    manifest = make_manifest(generation_id, payload_size=11)
    with pytest.raises(ValueError, match="payload_size"):
        manifest.validate_entries((make_entry(generation_id),))


def test_manifest_rejects_empty_manifest_in_mvp():
    with pytest.raises(ValueError, match="positive"):
        make_manifest(str(uuid4()), entry_count=0, payload_size=0)


def test_manifest_entry_rejects_invalid_hash_length():
    generation_id = str(uuid4())
    with pytest.raises(ValueError, match="32 bytes"):
        GenerationManifestEntry(
            generation_id=generation_id,
            record_id=str(uuid4()),
            object_id="object-A",
            version_id=str(uuid4()),
            chunk_id=str(uuid4()),
            offset=0,
            length=1,
            record_hash=b"short",
        )
