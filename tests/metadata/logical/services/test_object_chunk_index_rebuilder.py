from uuid import uuid4

import pytest

from engram.metadata.database import MetadataDB
from engram.metadata.logical.models import (
    Chunk,
    ChunkRef,
    Object,
    Version,
    VersionDelta,
)
from engram.metadata.logical.models.unsigned_version import UnsignedVersion
from engram.metadata.logical.repositories import (
    ChunkRepository,
    ObjectRepository,
    VersionDeltaRepository,
    VersionRepository,
)
from engram.metadata.logical.services import (
    Canonicalizer,
    Ed25519VersionSigner,
    ObjectChunkIndexRebuilder,
    ObjectChunkIndexHistoryError,
    ObjectChunkIndexTargetError,
    OrderKeyService,
)


@pytest.fixture
def db(tmp_path):
    database = MetadataDB(tmp_path / "metadata")
    yield database
    database.close()


def seed_one_version(db, *, corrupt_delta_root=False):
    object_id = "object-A"
    version_id = str(uuid4())
    chunk_id = str(uuid4())
    ref = ChunkRef(
        chunk_ref_id=str(uuid4()),
        chunk_id=chunk_id,
        order_key=OrderKeyService().generate_between(None, None),
    )
    signer = Ed25519VersionSigner.generate()
    delta = VersionDelta(
        object_id=object_id,
        version_id=version_id,
        sequence=1,
        added_chunks=(ref,),
        removed_chunk_ref_ids=(),
        created_at=2,
    )
    canonicalizer = Canonicalizer()
    delta_root = canonicalizer.hash_delta(delta)
    if corrupt_delta_root:
        delta_root = b"x" * 32
    unsigned = UnsignedVersion(
        version_id=version_id,
        object_id=object_id,
        sequence=1,
        parent_version_id=None,
        delta_root=delta_root,
        previous_version_hash=None,
        codec="test-v1",
        writer_public_key=signer.public_key,
        created_at=2,
    )
    signature = signer.sign(canonicalizer.encode_version_signing_payload(unsigned))
    version = Version(
        version_id=version_id,
        object_id=object_id,
        sequence=1,
        parent_version_id=None,
        delta_root=delta_root,
        previous_version_hash=None,
        codec="test-v1",
        writer_public_key=signer.public_key,
        signature=signature,
        created_at=2,
    )
    obj = Object(
        object_id=object_id,
        head_sequence=1,
        head_version_id=version_id,
        created_at=1,
        updated_at=2,
    )
    chunk = Chunk(
        object_id=object_id,
        chunk_id=chunk_id,
        content_hash=b"h" * 32,
        plaintext_size=10,
        created_at=1,
    )
    with db.batch() as batch:
        ObjectRepository(db).put(obj, batch)
        ChunkRepository(db).put(chunk, batch)
        VersionRepository(db).put(version, batch)
        VersionDeltaRepository(db).put(delta, batch)
        batch.commit()
    return obj, version, delta, ref


def test_reconstruct_replays_from_version_one_without_checkpoint(db):
    obj, version, _, ref = seed_one_version(db)
    result = ObjectChunkIndexRebuilder(db).reconstruct(obj.object_id, 1)

    assert result.sequence == 1
    assert result.version_id == version.version_id
    assert result.refs == (ref,)
    assert result.checkpoint_sequence == 0
    assert result.replayed_versions == 1
    assert result.persisted is False


def test_rebuild_current_replaces_stale_index_sequence(db):
    obj, _, _, ref = seed_one_version(db)
    result = ObjectChunkIndexRebuilder(db).rebuild_current(obj.object_id)

    from engram.metadata.logical.repositories import ObjectChunkIndexRepository

    index = ObjectChunkIndexRepository(db)
    assert result.persisted is True
    assert index.get_sequence(obj.object_id) == 1
    assert index.list_for_object(obj.object_id) == (ref,)


def test_reconstruct_rejects_delta_commitment_mismatch(db):
    obj, _, _, _ = seed_one_version(db, corrupt_delta_root=True)

    with pytest.raises(ObjectChunkIndexHistoryError, match="delta_root mismatch"):
        ObjectChunkIndexRebuilder(db).reconstruct(obj.object_id, 1)


def test_reconstruct_rejects_target_after_head(db):
    obj, _, _, _ = seed_one_version(db)

    with pytest.raises(ValueError, match="target_sequence"):
        ObjectChunkIndexRebuilder(db).reconstruct(obj.object_id, -1)
    with pytest.raises(ObjectChunkIndexTargetError, match="exceeds Object Head"):
        ObjectChunkIndexRebuilder(db).reconstruct(obj.object_id, 2)
