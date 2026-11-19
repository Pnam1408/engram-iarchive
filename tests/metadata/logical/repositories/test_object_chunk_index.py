from uuid import uuid4

import pytest
from fractional_indexing import generate_key_between

from engram.metadata.database import MetadataDB
from engram.metadata.logical.models import Chunk, ChunkRef, Object, VersionDelta
from engram.metadata.logical.repositories import (
    ChunkRepository,
    ObjectChunkIndexRepository,
)
from engram.metadata.logical.repositories.object_chunk_index import (
    ObjectChunkIndexError,
    ObjectChunkIndexSequenceConflictError,
)
from engram.metadata.logical.services import (
    MissingChunkCatalogRecordError,
    ObjectChunkIndexUpdater,
)


@pytest.fixture
def db(tmp_path):
    database = MetadataDB(tmp_path / "metadata.db")
    try:
        yield database
    finally:
        database.close()


def make_ref(chunk_id=None, order_key=None):
    return ChunkRef(
        chunk_ref_id=str(uuid4()),
        chunk_id=chunk_id or str(uuid4()),
        order_key=order_key or generate_key_between(None, None),
    )


def make_chunk(object_id, chunk_id, *, content_hash=b"h" * 32):
    return Chunk(
        object_id=object_id,
        chunk_id=chunk_id,
        content_hash=content_hash,
        plaintext_size=100,
        created_at=100,
    )


def test_index_repository_empty_index_has_sequence_zero(db):
    repo = ObjectChunkIndexRepository(db)
    assert repo.list_for_object("object-A") == ()
    assert repo.get_sequence("object-A") == 0


def test_updater_stages_added_refs_and_sequence_atomically(db):
    index = ObjectChunkIndexRepository(db)
    chunks = ChunkRepository(db)
    updater = ObjectChunkIndexUpdater(index, chunks)
    object_id = "object-A"
    ref = make_ref()
    delta = VersionDelta(
        object_id=object_id,
        version_id=str(uuid4()),
        sequence=1,
        added_chunks=(ref,),
        removed_chunk_ref_ids=(),
        created_at=101,
    )

    with db.batch() as batch:
        chunks.put(make_chunk(object_id, ref.chunk_id), batch)
        batch.commit()

    with db.batch() as batch:
        updated = updater.stage_delta(
            object_id=object_id,
            delta=delta,
            expected_sequence=0,
            batch=batch,
        )
        assert updated == (ref,)
        # No state is visible until the caller commits.
        assert index.get_sequence(object_id) == 0
        assert index.list_for_object(object_id) == ()
        batch.commit()

    assert index.get_sequence(object_id) == 1
    assert index.list_for_object(object_id) == (ref,)


def test_updater_applies_removal_and_can_reuse_removed_order_key(db):
    index = ObjectChunkIndexRepository(db)
    chunks = ChunkRepository(db)
    updater = ObjectChunkIndexUpdater(index, chunks)
    object_id = "object-A"
    old_ref = make_ref()
    new_ref = make_ref(order_key=old_ref.order_key)

    with db.batch() as batch:
        chunks.put(make_chunk(object_id, old_ref.chunk_id), batch)
        chunks.put(make_chunk(object_id, new_ref.chunk_id), batch)
        batch.commit()

    first = VersionDelta(
        object_id=object_id,
        version_id=str(uuid4()),
        sequence=1,
        added_chunks=(old_ref,),
        removed_chunk_ref_ids=(),
        created_at=101,
    )
    with db.batch() as batch:
        updater.stage_delta(
            object_id=object_id,
            delta=first,
            expected_sequence=0,
            batch=batch,
        )
        batch.commit()

    second = VersionDelta(
        object_id=object_id,
        version_id=str(uuid4()),
        sequence=2,
        added_chunks=(new_ref,),
        removed_chunk_ref_ids=(old_ref.chunk_ref_id,),
        created_at=102,
    )
    with db.batch() as batch:
        updated = updater.stage_delta(
            object_id=object_id,
            delta=second,
            expected_sequence=1,
            batch=batch,
        )
        assert updated == (new_ref,)
        batch.commit()

    assert index.get_sequence(object_id) == 2
    assert index.list_for_object(object_id) == (new_ref,)


def test_updater_rejects_missing_chunk_catalog_record_without_staging(db):
    index = ObjectChunkIndexRepository(db)
    updater = ObjectChunkIndexUpdater(index, ChunkRepository(db))
    ref = make_ref()
    delta = VersionDelta(
        object_id="object-A",
        version_id=str(uuid4()),
        sequence=1,
        added_chunks=(ref,),
        removed_chunk_ref_ids=(),
        created_at=101,
    )

    with db.batch() as batch:
        with pytest.raises(MissingChunkCatalogRecordError):
            updater.stage_delta(
                object_id="object-A",
                delta=delta,
                expected_sequence=0,
                batch=batch,
            )

    assert index.get_sequence("object-A") == 0
    assert index.list_for_object("object-A") == ()


def test_updater_rejects_stale_index_sequence(db):
    index = ObjectChunkIndexRepository(db)
    updater = ObjectChunkIndexUpdater(index, ChunkRepository(db))
    delta = VersionDelta(
        object_id="object-A",
        version_id=str(uuid4()),
        sequence=2,
        added_chunks=(),
        removed_chunk_ref_ids=(),
        created_at=102,
    )

    with db.batch() as batch:
        with pytest.raises(ObjectChunkIndexSequenceConflictError):
            updater.stage_delta(
                object_id="object-A",
                delta=delta,
                expected_sequence=1,
                batch=batch,
            )


def test_repository_rejects_duplicate_order_key_outside_removed_set(db):
    repo = ObjectChunkIndexRepository(db)
    first = make_ref()
    second = make_ref(order_key=first.order_key)

    with db.batch() as batch:
        repo.stage_changes(
            object_id="object-A",
            expected_sequence=0,
            new_sequence=1,
            removed_refs=(),
            added_refs=(first,),
            batch=batch,
        )
        batch.commit()

    with db.batch() as batch:
        with pytest.raises(ObjectChunkIndexError):
            repo.stage_changes(
                object_id="object-A",
                expected_sequence=1,
                new_sequence=2,
                removed_refs=(),
                added_refs=(second,),
                batch=batch,
            )


def test_repository_detects_entries_without_state_marker(db):
    repo = ObjectChunkIndexRepository(db)
    ref = make_ref()
    key = __import__(
        "engram.metadata.database", fromlist=["KeyCodec"]
    ).KeyCodec.object_chunk_index_key("object-A", ref.order_key)

    # Simulate malformed/legacy state: an entry without its sequence marker.
    from engram.metadata.database import ColumnFamily
    from engram.metadata.database.serialization import serialize

    db.put(
        ColumnFamily.OBJECT_CHUNK_INDEX,
        key,
        serialize(repo._entry_dict("object-A", ref)),
    )

    from engram.metadata.repositories import CorruptMetadataError

    with pytest.raises(CorruptMetadataError):
        repo.get_sequence("object-A")
