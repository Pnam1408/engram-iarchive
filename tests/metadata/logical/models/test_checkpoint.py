from uuid import uuid4

import pytest

from engram.metadata.logical.models import (
    Checkpoint,
    CheckpointEntry,
)


def test_create_checkpoint():
    checkpoint = Checkpoint(
        checkpoint_id=str(uuid4()),
        object_id="object-001",
        sequence=2,
        version_id=str(uuid4()),
        entry_count=3,
        snapshot_root=b"\x01" * 32,
        created_at=2000,
    )

    assert checkpoint.sequence == 2
    assert checkpoint.entry_count == 3


def test_create_empty_checkpoint():
    checkpoint = Checkpoint(
        checkpoint_id=str(uuid4()),
        object_id="object-001",
        sequence=1,
        version_id=str(uuid4()),
        entry_count=0,
        snapshot_root=b"\x00" * 32,
        created_at=1000,
    )

    assert checkpoint.entry_count == 0


def test_reject_invalid_snapshot_root():
    with pytest.raises(ValueError):
        Checkpoint(
            checkpoint_id=str(uuid4()),
            object_id="object-001",
            sequence=1,
            version_id=str(uuid4()),
            entry_count=0,
            snapshot_root=b"invalid",
            created_at=1000,
        )


def test_create_checkpoint_entry():
    entry = CheckpointEntry(
        checkpoint_id=str(uuid4()),
        chunk_ref_id=str(uuid4()),
        chunk_id=str(uuid4()),
        order_key="a0",
    )

    assert entry.order_key == "a0"
