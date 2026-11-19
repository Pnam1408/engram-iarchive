from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from engram.metadata.database import MetadataDB
from engram.metadata.logical.models import Object, Version, Chunk, ChunkRef, VersionDelta
from engram.metadata.logical.repositories import (
    ObjectRepository,
    VersionRepository,
    VersionDeltaRepository,
    ChunkRepository,
    ObjectChunkIndexRepository,
)
from engram.metadata.logical.services import (
    Canonicalizer,
    Ed25519VersionSigner,
    VersionBuilder,
    VersionBuildResult,
    VersionCommitService,
    ObjectNotFoundError,
    VersionHeadConflictError,
    InvalidVersionBuildResultError,
    PhysicalReadinessError,
    MissingChunkCatalogRecordError,
)


class Ready:
    def is_ready(self, result):
        return True


class NotReady:
    def is_ready(self, result):
        return False


class BrokenReadiness:
    def is_ready(self, result):
        raise RuntimeError("physical metadata unavailable")


@pytest.fixture
def db(tmp_path):
    database = MetadataDB(tmp_path / "metadata.db")
    try:
        yield database
    finally:
        database.close()


def create_object(db, object_id="object-A"):
    obj = Object(object_id, created_at=10, updated_at=10)
    with db.batch() as batch:
        ObjectRepository(db).put(obj, batch)
        batch.commit()
    return obj


def build_result(obj, *, parent=None, created_at=20, signer=None):
    signer = signer or Ed25519VersionSigner.generate()
    from engram.metadata.logical.models import VersionDelta

    delta = VersionDelta(
        object_id=obj.object_id,
        version_id=str(uuid4()),
        sequence=obj.head_sequence + 1,
        added_chunks=(),
        removed_chunk_ref_ids=(),
        created_at=created_at,
    )
    return VersionBuilder().build(
        object=obj,
        parent_version=parent,
        delta=delta,
        signer=signer,
    )


def test_commit_first_version_atomically_updates_three_records(db):
    obj = create_object(db)
    result = build_result(obj)
    service = VersionCommitService(db, Ready())

    committed = service.commit(result, expected_head_sequence=0)

    assert committed == result.version
    assert ObjectRepository(db).get(obj.object_id) == Object(
        obj.object_id,
        head_sequence=1,
        head_version_id=result.version.version_id,
        created_at=obj.created_at,
        updated_at=result.version.created_at,
    )
    assert VersionRepository(db).get(obj.object_id, 1) == result.version
    assert VersionDeltaRepository(db).get(obj.object_id, 1) == result.delta
    assert ObjectChunkIndexRepository(db).get_sequence(obj.object_id) == 1
    assert ObjectChunkIndexRepository(db).list_for_object(obj.object_id) == ()


def test_commit_second_version_links_current_head(db):
    obj = create_object(db)
    first = build_result(obj)
    VersionCommitService(db, Ready()).commit(first, expected_head_sequence=0)

    updated = ObjectRepository(db).get(obj.object_id)
    parent = VersionRepository(db).get_latest(obj.object_id)
    second = build_result(updated, parent=parent, created_at=30)

    committed = VersionCommitService(db, Ready()).commit(
        second,
        expected_head_sequence=1,
    )
    assert committed.sequence == 2
    assert committed.parent_version_id == first.version.version_id
    assert VersionRepository(db).get_latest(obj.object_id) == committed
    assert ObjectChunkIndexRepository(db).get_sequence(obj.object_id) == 2


def test_commit_rejects_missing_object(db):
    result = build_result(Object("missing", created_at=10, updated_at=10))
    with pytest.raises(ObjectNotFoundError):
        VersionCommitService(db, Ready()).commit(
            result,
            expected_head_sequence=0,
        )


def test_commit_rejects_stale_expected_head(db):
    obj = create_object(db)
    first = build_result(obj)
    VersionCommitService(db, Ready()).commit(first, expected_head_sequence=0)

    stale = build_result(obj)
    with pytest.raises(VersionHeadConflictError):
        VersionCommitService(db, Ready()).commit(
            stale,
            expected_head_sequence=0,
        )


@pytest.mark.parametrize("checker", [NotReady(), BrokenReadiness()])
def test_commit_does_not_write_anything_when_physical_data_is_not_ready(db, checker):
    obj = create_object(db)
    result = build_result(obj)

    with pytest.raises(PhysicalReadinessError):
        VersionCommitService(db, checker).commit(
            result,
            expected_head_sequence=0,
        )

    assert ObjectRepository(db).get(obj.object_id) == obj
    assert VersionRepository(db).get(obj.object_id, 1) is None
    assert VersionDeltaRepository(db).get(obj.object_id, 1) is None


def test_commit_rejects_tampered_delta_root(db):
    from dataclasses import replace

    obj = create_object(db)
    result = build_result(obj)
    tampered_version = replace(result.version, delta_root=b"x" * 32)
    # A newly constructed but incorrectly signed/inconsistent result is rejected.
    tampered = VersionBuildResult(tampered_version, result.delta)

    with pytest.raises(InvalidVersionBuildResultError):
        VersionCommitService(db, Ready()).commit(
            tampered,
            expected_head_sequence=0,
        )

    assert VersionRepository(db).get(obj.object_id, 1) is None


def test_commit_rejects_invalid_signature(db):
    from dataclasses import replace

    obj = create_object(db)
    result = build_result(obj)
    tampered_version = replace(result.version, signature=b"invalid-signature")
    tampered = VersionBuildResult(tampered_version, result.delta)

    with pytest.raises(InvalidVersionBuildResultError):
        VersionCommitService(db, Ready()).commit(
            tampered,
            expected_head_sequence=0,
        )


def test_two_concurrent_commits_for_same_head_only_one_succeeds(db):
    obj = create_object(db)
    result_a = build_result(obj)
    result_b = build_result(obj)
    service = VersionCommitService(db, Ready())

    def attempt(result):
        try:
            service.commit(result, expected_head_sequence=0)
            return "committed"
        except VersionHeadConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, (result_a, result_b)))

    assert sorted(outcomes) == ["committed", "conflict"]
    assert ObjectRepository(db).get(obj.object_id).head_sequence == 1
    assert len(VersionRepository(db).list_versions(obj.object_id)) == 1



def test_commit_atomically_updates_object_chunk_index_with_three_other_records(db):
    from engram.metadata.logical.models import ChunkRef, VersionDelta

    obj = create_object(db)
    chunk_id = str(uuid4())
    chunk_ref = ChunkRef(
        chunk_ref_id=str(uuid4()),
        chunk_id=chunk_id,
        order_key=__import__(
            "fractional_indexing"
        ).generate_key_between(None, None),
    )
    with db.batch() as batch:
        ChunkRepository(db).put(
            Chunk(
                object_id=obj.object_id,
                chunk_id=chunk_id,
                content_hash=b"c" * 32,
                plaintext_size=128,
                created_at=15,
            ),
            batch,
        )
        batch.commit()

    signer = Ed25519VersionSigner.generate()
    delta = VersionDelta(
        object_id=obj.object_id,
        version_id=str(uuid4()),
        sequence=1,
        added_chunks=(chunk_ref,),
        removed_chunk_ref_ids=(),
        created_at=20,
    )
    result = VersionBuilder().build(
        object=obj,
        parent_version=None,
        delta=delta,
        signer=signer,
    )

    committed = VersionCommitService(db, Ready()).commit(
        result,
        expected_head_sequence=0,
    )

    assert VersionRepository(db).get(obj.object_id, 1) == committed
    assert VersionDeltaRepository(db).get(obj.object_id, 1) == delta
    assert ObjectRepository(db).get(obj.object_id).head_version_id == committed.version_id
    assert ObjectChunkIndexRepository(db).get_sequence(obj.object_id) == 1
    assert ObjectChunkIndexRepository(db).list_for_object(obj.object_id) == (chunk_ref,)


def test_commit_missing_chunk_catalog_does_not_commit_any_of_four_records(db):
    obj = create_object(db)
    chunk_ref = ChunkRef(
        chunk_ref_id=str(uuid4()),
        chunk_id=str(uuid4()),
        order_key=__import__(
            "fractional_indexing"
        ).generate_key_between(None, None),
    )
    delta = VersionDelta(
        object_id=obj.object_id,
        version_id=str(uuid4()),
        sequence=1,
        added_chunks=(chunk_ref,),
        removed_chunk_ref_ids=(),
        created_at=20,
    )
    result = VersionBuilder().build(
        object=obj,
        parent_version=None,
        delta=delta,
        signer=Ed25519VersionSigner.generate(),
    )

    with pytest.raises(MissingChunkCatalogRecordError):
        VersionCommitService(db, Ready()).commit(
            result,
            expected_head_sequence=0,
        )

    assert ObjectRepository(db).get(obj.object_id) == obj
    assert VersionRepository(db).get(obj.object_id, 1) is None
    assert VersionDeltaRepository(db).get(obj.object_id, 1) is None
    assert ObjectChunkIndexRepository(db).get_sequence(obj.object_id) == 0
    assert ObjectChunkIndexRepository(db).list_for_object(obj.object_id) == ()
