from uuid import uuid4

import pytest
from fractional_indexing import generate_key_between

from engram.metadata.database import MetadataDB
from engram.metadata.repositories import RecordAlreadyExistsError
from engram.metadata.logical.models import Object, Version, VersionDelta, ChunkRef
from engram.metadata.logical.repositories import (
    ObjectRepository,
    VersionRepository,
    VersionDeltaRepository,
)


@pytest.fixture
def db(tmp_path):
    database = MetadataDB(tmp_path / "metadata.db")
    try:
        yield database
    finally:
        database.close()


def make_version(object_id: str, sequence: int, parent_id=None, previous_hash=None):
    return Version(
        version_id=str(uuid4()),
        object_id=object_id,
        sequence=sequence,
        parent_version_id=parent_id,
        delta_root=b"d" * 32,
        previous_version_hash=previous_hash,
        codec="ENGRAM-LOGICAL-METADATA/1",
        writer_public_key=b"public-key",
        signature=b"signature",
        created_at=100 + sequence,
    )


def make_delta(object_id: str, version: Version, added=(), removed=()):
    return VersionDelta(
        object_id=object_id,
        version_id=version.version_id,
        sequence=version.sequence,
        added_chunks=tuple(added),
        removed_chunk_ref_ids=tuple(removed),
        created_at=version.created_at,
    )


def test_object_repository_round_trip_and_head_update(db):
    repo = ObjectRepository(db)
    obj = Object("object-A", created_at=10, updated_at=10)
    with db.batch() as batch:
        repo.put(obj, batch)
        batch.commit()

    assert repo.get("object-A") == obj
    assert repo.exists("object-A")

    version = make_version("object-A", 1)
    updated = Object(
        object_id=obj.object_id,
        head_sequence=1,
        head_version_id=version.version_id,
        created_at=obj.created_at,
        updated_at=version.created_at,
    )
    with db.batch() as batch:
        repo.put(updated, batch)
        batch.commit()
    assert repo.get("object-A") == updated


def test_object_repository_missing_returns_none(db):
    repo = ObjectRepository(db)
    assert repo.get("missing") is None
    assert not repo.exists("missing")


def test_object_write_requires_explicit_commit(db):
    repo = ObjectRepository(db)
    obj = Object("object-A", created_at=1, updated_at=1)
    with db.batch() as batch:
        repo.put(obj, batch)
    assert repo.get("object-A") is None


def test_version_repository_round_trip_and_ordered_listing(db):
    repo = VersionRepository(db)
    object_id = "object-A"
    v1 = make_version(object_id, 1)
    v2 = make_version(object_id, 2, v1.version_id, b"h" * 32)
    with db.batch() as batch:
        repo.put(v1, batch)
        repo.put(v2, batch)
        batch.commit()

    assert repo.get(object_id, 1) == v1
    assert repo.get_by_id(object_id, v2.version_id) == v2
    assert repo.list_versions(object_id) == (v1, v2)
    assert repo.list_versions(object_id, start_sequence=2, limit=1) == (v2,)
    assert repo.list_versions(object_id, limit=0) == ()


def test_version_repository_rejects_overwrite(db):
    repo = VersionRepository(db)
    version = make_version("object-A", 1)
    with db.batch() as batch:
        repo.put(version, batch)
        batch.commit()
    with db.batch() as batch:
        with pytest.raises(RecordAlreadyExistsError):
            repo.put(version, batch)


def test_version_get_latest_uses_object_head(db):
    objects = ObjectRepository(db)
    versions = VersionRepository(db)
    obj = Object("object-A", created_at=1, updated_at=1)
    v1 = make_version("object-A", 1)
    updated = Object("object-A", 1, v1.version_id, 1, v1.created_at)
    with db.batch() as batch:
        objects.put(obj, batch)
        versions.put(v1, batch)
        objects.put(updated, batch)
        batch.commit()
    assert versions.get_latest("object-A") == v1


def test_version_delta_round_trip(db):
    repo = VersionDeltaRepository(db)
    object_id = "object-A"
    version = make_version(object_id, 1)
    ref = ChunkRef(str(uuid4()), str(uuid4()), generate_key_between(None, None))
    delta = make_delta(object_id, version, added=(ref,))
    with db.batch() as batch:
        repo.put(delta, batch)
        batch.commit()
    assert repo.get(object_id, 1) == delta
    assert repo.get_by_version_id(object_id, version.version_id) == delta


def test_version_delta_empty_round_trip(db):
    repo = VersionDeltaRepository(db)
    version = make_version("object-A", 1)
    delta = make_delta("object-A", version)
    with db.batch() as batch:
        repo.put(delta, batch)
        batch.commit()
    assert repo.get("object-A", 1) == delta


def test_version_delta_rejects_overwrite(db):
    repo = VersionDeltaRepository(db)
    version = make_version("object-A", 1)
    delta = make_delta("object-A", version)
    with db.batch() as batch:
        repo.put(delta, batch)
        batch.commit()
    with db.batch() as batch:
        with pytest.raises(RecordAlreadyExistsError):
            repo.put(delta, batch)


def test_all_three_repositories_share_one_atomic_batch(db):
    objects = ObjectRepository(db)
    versions = VersionRepository(db)
    deltas = VersionDeltaRepository(db)
    obj = Object("object-A", created_at=1, updated_at=1)
    version = make_version("object-A", 1)
    delta = make_delta("object-A", version)
    updated = Object("object-A", 1, version.version_id, 1, version.created_at)

    with db.batch() as batch:
        versions.put(version, batch)
        deltas.put(delta, batch)
        objects.put(updated, batch)
        batch.commit()

    assert objects.get("object-A") == updated
    assert versions.get("object-A", 1) == version
    assert deltas.get("object-A", 1) == delta
