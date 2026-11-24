import uuid
import pytest
from engram.commitment.mmr import MMRState, Peak
from engram.metadata.database import ColumnFamily
from engram.metadata.mmr.models import MMRStateRecord, MMRNodeRecord, MMR_PROTOCOL
from engram.metadata.mmr.repositories import MMRStateRepository, MMRNodeRepository
from engram.metadata.repositories import CorruptMetadataError


def oid():
    return str(uuid.uuid4())


def test_mmr_state_repository_batch_upsert_and_roundtrip(metadata_env):
    object_id = oid()
    repo = MMRStateRepository(metadata_env)
    record = MMRStateRecord(object_id, MMR_PROTOCOL, MMRState(3, (Peak(1, b"a" * 32), Peak(0, b"b" * 32))))
    assert repo.get(object_id) is None
    with metadata_env.batch() as batch:
        repo.put(record, batch)
        batch.commit()
    assert repo.exists(object_id)
    assert repo.get(object_id) == record
    replacement = MMRStateRecord(object_id, MMR_PROTOCOL, MMRState(4, (Peak(2, b"c" * 32),)))
    with metadata_env.batch() as batch:
        repo.put(replacement, batch)
        batch.commit()
    assert repo.get(object_id) == replacement


def test_state_repository_missing_is_none(metadata_env):
    assert MMRStateRepository(metadata_env).get(oid()) is None



def test_mmr_node_repository_insert_get_and_list(metadata_env):
    object_id = oid()
    repo = MMRNodeRepository(metadata_env)
    records = (MMRNodeRecord(object_id, 0, 1, b"a" * 32), MMRNodeRecord(object_id, 4, 2, b"b" * 32))
    with metadata_env.batch() as batch:
        repo.put_all(records, batch)
        batch.commit()
    assert repo.get(object_id, 0, 1) == records[0]
    assert repo.get(object_id, 4, 2) == records[1]
    assert repo.list_for_object(object_id) == records



def test_node_immutable_same_hash_idempotent_different_hash_rejected(metadata_env):
    object_id = oid()
    repo = MMRNodeRepository(metadata_env)
    record = MMRNodeRecord(object_id, 0, 1, b"a" * 32)
    with metadata_env.batch() as batch:
        repo.put(record, batch)
        batch.commit()
    with metadata_env.batch() as batch:
        repo.put(record, batch)
        batch.commit()
    with pytest.raises(CorruptMetadataError):
        with metadata_env.batch() as batch:
            repo.put(MMRNodeRecord(object_id, 0, 1, b"z" * 32), batch)


def test_put_all_preflight_rejects_duplicate_coordinates(metadata_env):
    object_id = oid()
    repo = MMRNodeRepository(metadata_env)
    duplicated = (MMRNodeRecord(object_id, 0, 1, b"a" * 32), MMRNodeRecord(object_id, 0, 1, b"b" * 32))
    with pytest.raises(ValueError):
        with metadata_env.batch() as batch:
            repo.put_all(duplicated, batch)


def test_empty_mmr_state_is_not_persisted(metadata_env):
    object_id = oid()
    repo = MMRStateRepository(metadata_env)
    empty = MMRStateRecord(object_id, MMR_PROTOCOL, MMRState(0, ()))
    with pytest.raises(ValueError, match="Empty MMR state"):
        with metadata_env.batch() as batch:
            repo.put(empty, batch)
    assert repo.get(object_id) is None
