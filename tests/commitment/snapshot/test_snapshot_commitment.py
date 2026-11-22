from uuid import UUID, uuid4

import pytest
from fractional_indexing import generate_key_between

from engram.commitment.snapshot import SnapshotCanonicalizer, SnapshotCommitmentService
from engram.metadata.logical.models import ChunkRef


def make_ref(order_key=None):
    return ChunkRef(
        chunk_ref_id=str(uuid4()),
        chunk_id=str(uuid4()),
        order_key=order_key or generate_key_between(None, None),
    )


def test_snapshot_hash_is_deterministic_and_order_independent():
    first = make_ref(generate_key_between(None, None))
    second = make_ref(generate_key_between(first.order_key, None))
    args = dict(object_id="object-A", sequence=4, version_id=str(uuid4()))
    canonicalizer = SnapshotCanonicalizer()

    left = canonicalizer.hash(**args, chunk_refs=(first, second))
    right = canonicalizer.hash(**args, chunk_refs=(second, first))

    assert left == right
    assert len(left) == 32


def test_snapshot_commits_all_chunk_ref_fields():
    ref = make_ref()
    args = dict(object_id="object-A", sequence=1, version_id=str(uuid4()))
    original = SnapshotCanonicalizer.hash(**args, chunk_refs=(ref,))

    changed_chunk = ChunkRef(ref.chunk_ref_id, str(uuid4()), ref.order_key)
    changed_order = ChunkRef(
        ref.chunk_ref_id,
        ref.chunk_id,
        generate_key_between(ref.order_key, None),
    )

    assert SnapshotCanonicalizer.hash(**args, chunk_refs=(changed_chunk,)) != original
    assert SnapshotCanonicalizer.hash(**args, chunk_refs=(changed_order,)) != original


def test_snapshot_binds_object_version_and_sequence():
    ref = make_ref()
    args = dict(object_id="object-A", sequence=1, version_id=str(uuid4()))
    original = SnapshotCanonicalizer.hash(**args, chunk_refs=(ref,))

    assert (
        SnapshotCanonicalizer.hash(
            **{**args, "object_id": "object-B"}, chunk_refs=(ref,)
        )
        != original
    )
    assert (
        SnapshotCanonicalizer.hash(**{**args, "sequence": 2}, chunk_refs=(ref,))
        != original
    )
    assert (
        SnapshotCanonicalizer.hash(
            **{**args, "version_id": str(uuid4())}, chunk_refs=(ref,)
        )
        != original
    )


def test_empty_snapshot_is_valid_and_bound_to_version():
    args = dict(object_id="object-A", sequence=1, version_id=str(uuid4()))
    root = SnapshotCanonicalizer.hash(**args, chunk_refs=())
    assert len(root) == 32
    assert root != SnapshotCanonicalizer.hash(**{**args, "sequence": 2}, chunk_refs=())


def test_duplicate_chunk_ref_id_is_rejected():
    ref = make_ref()
    duplicate = ChunkRef(
        ref.chunk_ref_id, str(uuid4()), generate_key_between(ref.order_key, None)
    )
    with pytest.raises(ValueError, match="duplicate ChunkRefID"):
        SnapshotCanonicalizer.hash(
            object_id="object-A",
            sequence=1,
            version_id=str(uuid4()),
            chunk_refs=(ref, duplicate),
        )


def test_duplicate_order_key_is_rejected():
    ref = make_ref()
    duplicate = ChunkRef(str(uuid4()), str(uuid4()), ref.order_key)
    with pytest.raises(ValueError, match="duplicate OrderKey"):
        SnapshotCanonicalizer.hash(
            object_id="object-A",
            sequence=1,
            version_id=str(uuid4()),
            chunk_refs=(ref, duplicate),
        )


def test_service_verifies_expected_root():
    ref = make_ref()
    service = SnapshotCommitmentService()
    args = dict(
        object_id="object-A", sequence=1, version_id=str(uuid4()), chunk_refs=(ref,)
    )
    root = service.compute_snapshot_root(**args)

    assert service.verify_snapshot(**args, expected_root=root)
    assert not service.verify_snapshot(**args, expected_root=b"x" * 32)


def test_snapshot_canonical_bytes_use_uuid_binary_representation():
    ref = make_ref()
    encoded = SnapshotCanonicalizer.encode(
        object_id="object-A", sequence=1, version_id=str(uuid4()), chunk_refs=(ref,)
    )
    assert UUID(ref.chunk_ref_id).bytes in encoded
    assert UUID(ref.chunk_id).bytes in encoded
