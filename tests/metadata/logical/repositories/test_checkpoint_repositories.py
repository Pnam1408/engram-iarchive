from uuid import uuid4

import pytest

from engram.metadata.database import ColumnFamily, KeyCodec, MetadataDB
from engram.metadata.database.serialization import serialize
from engram.metadata.logical.models import Checkpoint, CheckpointEntry
from engram.metadata.logical.repositories import (
    CheckpointRepository,
    CheckpointEntryRepository,
)
from engram.metadata.logical.services.order_key import OrderKeyService
from engram.metadata.repositories import CorruptMetadataError, RecordAlreadyExistsError


@pytest.fixture
def db(tmp_path):
    database = MetadataDB(tmp_path / "metadata.db")
    try:
        yield database
    finally:
        database.close()


def make_checkpoint(sequence=1, entry_count=1):
    return Checkpoint(
        checkpoint_id=str(uuid4()),
        object_id="object-A",
        sequence=sequence,
        version_id=str(uuid4()),
        entry_count=entry_count,
        snapshot_root=b"r" * 32,
        created_at=100 + sequence,
    )


def make_entry(checkpoint, order_key=None):
    if order_key is None:
        order_key = OrderKeyService().generate_between(None, None)
    return CheckpointEntry(
        checkpoint_id=checkpoint.checkpoint_id,
        chunk_ref_id=str(uuid4()),
        chunk_id=str(uuid4()),
        order_key=order_key,
    )


def test_checkpoint_repository_round_trip_and_ordered_listing(db):
    repo = CheckpointRepository(db)
    first = make_checkpoint(1)
    second = make_checkpoint(3)
    with db.batch() as batch:
        repo.put(first, batch)
        repo.put(second, batch)
        batch.commit()
    assert repo.get("object-A", 1) == first
    assert repo.list_for_object("object-A") == (first, second)
    assert repo.latest_before("object-A", 2) == first
    assert repo.latest_before("object-A", 0) is None


def test_checkpoint_repository_rejects_overwrite(db):
    repo = CheckpointRepository(db)
    checkpoint = make_checkpoint()
    with db.batch() as batch:
        repo.put(checkpoint, batch)
        batch.commit()
    with db.batch() as batch:
        with pytest.raises(RecordAlreadyExistsError):
            repo.put(checkpoint, batch)


def test_checkpoint_repository_uncommitted_write_is_invisible(db):
    repo = CheckpointRepository(db)
    checkpoint = make_checkpoint()
    with db.batch() as batch:
        repo.put(checkpoint, batch)
    assert repo.get("object-A", 1) is None


def test_checkpoint_repository_detects_key_value_mismatch(db):
    repo = CheckpointRepository(db)
    checkpoint = make_checkpoint()
    with db.batch() as batch:
        repo.put(checkpoint, batch)
        batch.commit()
    wrong = make_checkpoint(sequence=2)
    db.put(
        ColumnFamily.CHECKPOINTS,
        KeyCodec.checkpoint_key("object-A", 1),
        serialize(repo._to_dict(wrong)),
    )
    with pytest.raises(CorruptMetadataError):
        repo.get("object-A", 1)


def test_checkpoint_entry_repository_round_trip_and_empty_snapshot(db):
    cp_repo = CheckpointRepository(db)
    entry_repo = CheckpointEntryRepository(db)
    checkpoint = make_checkpoint(entry_count=2)
    order_keys = OrderKeyService().generate_n_between(None, None, 2)
    entries = tuple(make_entry(checkpoint, key) for key in order_keys)
    with db.batch() as batch:
        cp_repo.put(checkpoint, batch)
        entry_repo.put_all(checkpoint, entries, batch)
        batch.commit()
    assert entry_repo.list_for_checkpoint(checkpoint) == entries

    empty = make_checkpoint(sequence=2, entry_count=0)
    with db.batch() as batch:
        cp_repo.put(empty, batch)
        entry_repo.put_all(empty, (), batch)
        batch.commit()
    assert entry_repo.list_for_checkpoint(empty) == ()


def test_checkpoint_entry_repository_rejects_wrong_checkpoint_id(db):
    checkpoint = make_checkpoint()
    entry = make_entry(make_checkpoint())
    with db.batch() as batch:
        with pytest.raises(ValueError):
            CheckpointEntryRepository(db).put_all(checkpoint, (entry,), batch)


def test_checkpoint_entry_repository_detects_entry_count_mismatch(db):
    checkpoint = make_checkpoint(entry_count=2)
    entry = make_entry(checkpoint)
    with db.batch() as batch:
        CheckpointRepository(db).put(checkpoint, batch)
        second_key = OrderKeyService().generate_between(entry.order_key, None)
        CheckpointEntryRepository(db).put_all(
            checkpoint, (entry, make_entry(checkpoint, second_key)), batch
        )
        batch.commit()
    corrupted = Checkpoint(
        checkpoint_id=checkpoint.checkpoint_id,
        object_id=checkpoint.object_id,
        sequence=checkpoint.sequence,
        version_id=checkpoint.version_id,
        entry_count=1,
        snapshot_root=checkpoint.snapshot_root,
        created_at=checkpoint.created_at,
    )
    with pytest.raises(CorruptMetadataError):
        CheckpointEntryRepository(db).list_for_checkpoint(corrupted)
