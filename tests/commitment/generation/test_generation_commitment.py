from dataclasses import replace
from uuid import uuid4

import pytest

from engram.commitment.generation import (
    GenerationCanonicalizer,
    GenerationCommitmentService,
)
from engram.metadata.physical.models import (
    Generation,
    GenerationManifest,
    GenerationManifestEntry,
    GenerationStatus,
)


def build_fixture():
    gid = str(uuid4())
    generation = Generation(
        generation_id=gid,
        data_shards=3,
        total_shards=5,
        status=GenerationStatus.ENCODED,
        created_at=10,
        updated_at=12,
        sealed_at=11,
        encoded_at=12,
    )
    entries = (
        GenerationManifestEntry(
            generation_id=gid, record_id=str(uuid4()), object_id="object-a",
            chunk_id=str(uuid4()), offset=0, length=7, record_hash=b"a" * 32,
        ),
        GenerationManifestEntry(
            generation_id=gid, record_id=str(uuid4()), object_id="object-b",
            chunk_id=str(uuid4()), offset=7, length=9, record_hash=b"b" * 32,
        ),
    )
    manifest = GenerationManifest(
        generation_id=gid, entry_count=2, payload_size=16,
        manifest_root=b"x" * 32, created_at=13,
    )
    return generation, manifest, entries


def test_canonicalizer_hashes_complete_manifest_and_configuration():
    generation, manifest, entries = build_fixture()
    canonicalizer = GenerationCanonicalizer()
    root = canonicalizer.hash(generation=generation, manifest=manifest, entries=entries)
    assert len(root) == 32
    assert root == canonicalizer.hash(generation=generation, manifest=manifest, entries=entries)


def test_commitment_verifies_manifest_root():
    generation, manifest, entries = build_fixture()
    service = GenerationCommitmentService()
    root = service.compute_manifest_root(generation=generation, manifest=manifest, entries=entries)
    assert service.verify_manifest_root(
        generation=generation, manifest=manifest, entries=entries, expected_root=root
    )
    assert not service.verify_manifest_root(
        generation=generation, manifest=manifest, entries=entries, expected_root=b"z" * 32
    )


def test_generation_ec_configuration_is_committed():
    generation, manifest, entries = build_fixture()
    service = GenerationCommitmentService()
    original = service.compute_manifest_root(generation=generation, manifest=manifest, entries=entries)
    altered = replace(generation, data_shards=4, total_shards=6)
    changed = service.compute_manifest_root(generation=altered, manifest=manifest, entries=entries)
    assert original != changed


def test_timestamps_and_stored_root_are_not_committed():
    generation, manifest, entries = build_fixture()
    service = GenerationCommitmentService()
    original = service.compute_manifest_root(generation=generation, manifest=manifest, entries=entries)
    altered_generation = replace(generation, created_at=9, updated_at=14, sealed_at=10, encoded_at=14)
    altered_manifest = replace(manifest, created_at=99, manifest_root=b"q" * 32)
    assert original == service.compute_manifest_root(
        generation=altered_generation, manifest=altered_manifest, entries=entries
    )


def test_entry_metadata_and_record_hash_are_committed():
    generation, manifest, entries = build_fixture()
    service = GenerationCommitmentService()
    original = service.compute_manifest_root(generation=generation, manifest=manifest, entries=entries)
    altered_entry = replace(entries[0], record_hash=b"z" * 32)
    altered_entries = (altered_entry, entries[1])
    changed = service.compute_manifest_root(generation=generation, manifest=manifest, entries=altered_entries)
    assert original != changed


def test_entries_are_canonicalized_by_offset():
    generation, manifest, entries = build_fixture()
    canonicalizer = GenerationCanonicalizer()
    assert canonicalizer.encode(generation=generation, manifest=manifest, entries=entries) == canonicalizer.encode(
        generation=generation, manifest=manifest, entries=tuple(reversed(entries))
    )

def test_manifest_validation_is_required_before_hashing():
    generation, manifest, entries = build_fixture()
    broken = (replace(entries[1], offset=8), entries[0])
    with pytest.raises(ValueError):
        GenerationCanonicalizer.encode(generation=generation, manifest=manifest, entries=broken)

def test_generation_and_manifest_identity_must_match():
    generation, manifest, entries = build_fixture()
    mismatched = replace(manifest, generation_id=str(uuid4()))
    with pytest.raises(ValueError, match="IDs do not match"):
        GenerationCanonicalizer.encode(generation=generation, manifest=mismatched, entries=entries)
