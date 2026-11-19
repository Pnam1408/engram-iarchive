from uuid import uuid4

import pytest

from engram.metadata.logical.models import (
    ChunkRef,
    VersionDelta,
)


def make_ref(order_key: str = "a0") -> ChunkRef:
    return ChunkRef(
        chunk_ref_id=str(uuid4()),
        chunk_id=str(uuid4()),
        order_key=order_key,
    )


def test_create_version_delta():
    ref = make_ref()

    delta = VersionDelta(
        object_id="object-001",
        version_id=str(uuid4()),
        sequence=1,
        added_chunks=(ref,),
        removed_chunk_ref_ids=(),
        created_at=1000,
    )

    assert len(delta.added_chunks) == 1
    assert delta.removed_chunk_ref_ids == ()


def test_reject_duplicate_added_chunk_ref_ids():
    ref = make_ref()

    duplicate = ChunkRef(
        chunk_ref_id=ref.chunk_ref_id,
        chunk_id=str(uuid4()),
        order_key="a1",
    )

    with pytest.raises(ValueError):
        VersionDelta(
            object_id="object-001",
            version_id=str(uuid4()),
            sequence=1,
            added_chunks=(ref, duplicate),
            removed_chunk_ref_ids=(),
            created_at=1000,
        )


def test_reject_duplicate_added_order_keys():
    with pytest.raises(ValueError):
        VersionDelta(
            object_id="object-001",
            version_id=str(uuid4()),
            sequence=1,
            added_chunks=(make_ref("a0"), make_ref("a0")),
            removed_chunk_ref_ids=(),
            created_at=1000,
        )


def test_reject_same_ref_added_and_removed():
    ref = make_ref()

    with pytest.raises(ValueError):
        VersionDelta(
            object_id="object-001",
            version_id=str(uuid4()),
            sequence=2,
            added_chunks=(ref,),
            removed_chunk_ref_ids=(ref.chunk_ref_id,),
            created_at=2000,
        )


def test_reject_removal_in_first_version():
    with pytest.raises(ValueError):
        VersionDelta(
            object_id="object-001",
            version_id=str(uuid4()),
            sequence=1,
            added_chunks=(),
            removed_chunk_ref_ids=(str(uuid4()),),
            created_at=1000,
        )
