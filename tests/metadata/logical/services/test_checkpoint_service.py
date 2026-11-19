from uuid import uuid4

import pytest
from fractional_indexing import generate_key_between

from engram.metadata.database import MetadataDB
from engram.metadata.logical.models import Chunk, Object, Version, ChunkRef
from engram.metadata.logical.repositories import (
    ObjectRepository,
    VersionRepository,
    ChunkRepository,
    ObjectChunkIndexRepository,
    CheckpointRepository,
    CheckpointEntryRepository,
)
from engram.metadata.logical.services import (
    CheckpointBuilder,
    CheckpointVerifier,
    CheckpointService,
    CheckpointHeadMismatchError,
    CheckpointVerificationError,
)


@pytest.fixture
def db(tmp_path):
    database = MetadataDB(tmp_path / "metadata.db")
    try:
        yield database
    finally:
        database.close()


def seed_head(db, refs=()):
    object_id = "object-A"
    version = Version(
        version_id=str(uuid4()),
        object_id=object_id,
        sequence=1,
        parent_version_id=None,
        delta_root=b"d" * 32,
        previous_version_hash=None,
        codec="ENGRAM-LOGICAL-METADATA/1",
        writer_public_key=b"pub",
        signature=b"sig",
        created_at=100,
    )
    obj = Object(object_id, 1, version.version_id, 1, 100)
    objects = ObjectRepository(db)
    versions = VersionRepository(db)
    chunks = ChunkRepository(db)
    index = ObjectChunkIndexRepository(db)
    with db.batch() as batch:
        objects.put(obj, batch)
        versions.put(version, batch)
        for ref in refs:
            chunks.put(Chunk(object_id, ref.chunk_id, b"h" * 32, 4, 90), batch)
        index.stage_changes(
            object_id=object_id,
            expected_sequence=0,
            new_sequence=1,
            removed_refs=(),
            added_refs=tuple(refs),
            batch=batch,
        )
        batch.commit()
    return obj, version


def test_checkpoint_service_creates_and_verifies_snapshot(db):
    ref1 = ChunkRef(str(uuid4()), str(uuid4()), generate_key_between(None, None))
    ref2 = ChunkRef(
        str(uuid4()), str(uuid4()), generate_key_between(ref1.order_key, None)
    )
    obj, version = seed_head(db, (ref1, ref2))
    service = CheckpointService(db)
    checkpoint = service.create_for_head(obj.object_id, created_at=200)
    entries = service.verify(checkpoint)
    assert checkpoint.sequence == 1
    assert checkpoint.version_id == version.version_id
    assert checkpoint.entry_count == 2
    assert [entry.chunk_ref_id for entry in entries] == [
        ref1.chunk_ref_id,
        ref2.chunk_ref_id,
    ]


def test_checkpoint_service_supports_empty_object_snapshot(db):
    obj, _ = seed_head(db, ())
    checkpoint = CheckpointService(db).create_for_head(obj.object_id, created_at=200)
    assert checkpoint.entry_count == 0
    assert CheckpointService(db).verify(checkpoint) == ()


def test_checkpoint_service_rejects_unsynchronized_index(db):
    obj, _ = seed_head(db, ())
    index = ObjectChunkIndexRepository(db)
    with db.batch() as batch:
        # Force an index state marker that no longer represents Object Head.
        batch.put(
            "object_chunk_index",
            __import__(
                "engram.metadata.database", fromlist=["KeyCodec"]
            ).KeyCodec.object_chunk_index_state_key(obj.object_id),
            b'{"object_id":"object-A","sequence":0}',
        )
        batch.commit()
    with pytest.raises(CheckpointHeadMismatchError):
        CheckpointService(db).create_for_head(obj.object_id, created_at=200)


def test_checkpoint_verifier_rejects_snapshot_tampering(db):
    obj, _ = seed_head(db, ())
    service = CheckpointService(db)
    checkpoint = service.create_for_head(obj.object_id, created_at=200)
    repo = CheckpointEntryRepository(db)
    entries = repo.list_for_checkpoint(checkpoint)
    bad = type(checkpoint)(
        checkpoint_id=checkpoint.checkpoint_id,
        object_id=checkpoint.object_id,
        sequence=checkpoint.sequence,
        version_id=checkpoint.version_id,
        entry_count=1,
        snapshot_root=checkpoint.snapshot_root,
        created_at=checkpoint.created_at,
    )
    with pytest.raises(CheckpointVerificationError):
        CheckpointVerifier().verify(bad, entries)


def test_checkpoint_service_latest_valid_before(db):
    obj, _ = seed_head(db, ())
    service = CheckpointService(db)
    cp1 = service.create_for_head(obj.object_id, created_at=200)
    found = service.latest_valid_before(obj.object_id, 1)
    assert found is not None
    assert found[0] == cp1
    assert service.latest_valid_before(obj.object_id, 0) is None
