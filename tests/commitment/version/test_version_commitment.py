from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from engram.commitment.version import (
    DeltaCanonicalizer,
    VersionCanonicalizer,
    VersionCommitmentService,
)
from engram.metadata.logical.models import ChunkRef, Version, VersionDelta


def uid() -> str:
    return str(uuid4())


def make_delta(*, created_at=100, sequence=1, added=(), removed=()):
    return VersionDelta(
        object_id="object-A",
        version_id=uid(),
        sequence=sequence,
        added_chunks=tuple(added),
        removed_chunk_ref_ids=tuple(removed),
        created_at=created_at,
    )


def make_version(delta: VersionDelta) -> Version:
    return Version(
        version_id=delta.version_id,
        object_id=delta.object_id,
        sequence=delta.sequence,
        parent_version_id=None,
        delta_root=DeltaCanonicalizer.hash(delta),
        previous_version_hash=None,
        codec="ENGRAM-LOGICAL-METADATA/1",
        writer_public_key=b"p" * 32,
        signature=b"signature",
        created_at=delta.created_at,
    )


def test_delta_commitment_excludes_created_at():
    delta = make_delta()
    later = replace(delta, created_at=999999)
    assert DeltaCanonicalizer.hash(delta) == DeltaCanonicalizer.hash(later)


def test_delta_commitment_is_order_independent_for_added_and_removed_sets():
    a = ChunkRef(uid(), uid(), "a0")
    b = ChunkRef(uid(), uid(), "a1")
    r1, r2 = uid(), uid()
    left = make_delta(sequence=2, added=(a, b), removed=(r1, r2))
    right = replace(left, added_chunks=(b, a), removed_chunk_ref_ids=(r2, r1))
    assert DeltaCanonicalizer.hash(left) == DeltaCanonicalizer.hash(right)


def test_delta_commitment_changes_when_delta_content_changes():
    ref = ChunkRef(uid(), uid(), "a0")
    delta = make_delta(added=(ref,))
    changed = replace(delta, added_chunks=(replace(ref, order_key="a1"),))
    assert DeltaCanonicalizer.hash(delta) != DeltaCanonicalizer.hash(changed)


def test_version_manifest_excludes_timestamp_signer_and_signature():
    delta = make_delta()
    version = make_version(delta)
    changed = replace(
        version,
        created_at=987654,
        writer_public_key=b"q" * 32,
        signature=b"different signature",
    )
    assert VersionCanonicalizer.hash(version) == VersionCanonicalizer.hash(changed)


def test_version_manifest_changes_when_committed_field_changes():
    version = make_version(make_delta())
    changed = replace(version, codec="another-codec")
    assert VersionCanonicalizer.hash(version) != VersionCanonicalizer.hash(changed)


def test_service_verifies_delta_identity_and_root():
    delta = make_delta()
    version = make_version(delta)
    service = VersionCommitmentService()
    assert service.verify_delta(version, delta)
    assert service.verify_delta(
        version, replace(delta, created_at=200)
    )  # timestamp is not committed
    with pytest.raises(ValueError):
        service.verify_delta(version, replace(delta, object_id="other"))


def test_uuid_fields_are_encoded_as_uuid_bytes():
    delta = make_delta()
    encoded = DeltaCanonicalizer.encode(delta)
    assert UUID(delta.version_id).bytes in encoded
