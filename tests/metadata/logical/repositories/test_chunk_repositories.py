import base64
import uuid

import pytest

from engram.metadata.database import ColumnFamily, KeyCodec, MetadataDB, serialize
from engram.metadata.logical.models import Chunk
from engram.metadata.logical.repositories import (
    ChunkDedupIndexRepository,
    ChunkRepository,
)
from engram.metadata.repositories import (
    CorruptMetadataError,
    RecordAlreadyExistsError,
)


@pytest.fixture
def db(tmp_path):
    database = MetadataDB(tmp_path / "metadata.db")
    try:
        yield database
    finally:
        database.close()


def make_chunk(
    object_id="object-A",
    *,
    chunk_id=None,
    content_hash=b"h" * 32,
    plaintext_size=4096,
    created_at=100,
):
    return Chunk(
        object_id=object_id,
        chunk_id=chunk_id or str(uuid.uuid4()),
        content_hash=content_hash,
        plaintext_size=plaintext_size,
        created_at=created_at,
    )


def test_chunk_repository_round_trip_and_exists(db):
    repo = ChunkRepository(db)
    chunk = make_chunk()

    assert repo.get(chunk.object_id, chunk.chunk_id) is None
    assert not repo.exists(chunk.object_id, chunk.chunk_id)

    with db.batch() as batch:
        repo.put(chunk, batch)
        batch.commit()

    assert repo.get(chunk.object_id, chunk.chunk_id) == chunk
    assert repo.exists(chunk.object_id, chunk.chunk_id)


def test_chunk_repository_does_not_overwrite_existing_chunk(db):
    repo = ChunkRepository(db)
    chunk = make_chunk()
    with db.batch() as batch:
        repo.put(chunk, batch)
        batch.commit()

    changed = Chunk(
        object_id=chunk.object_id,
        chunk_id=chunk.chunk_id,
        content_hash=b"x" * 32,
        plaintext_size=chunk.plaintext_size,
        created_at=chunk.created_at,
    )
    with db.batch() as batch:
        with pytest.raises(RecordAlreadyExistsError):
            repo.put(changed, batch)


def test_chunk_repository_rejects_non_chunk(db):
    with db.batch() as batch:
        with pytest.raises(TypeError):
            ChunkRepository(db).put(object(), batch)


def test_chunk_repository_detects_key_value_mismatch(db):
    chunk = make_chunk()
    key = KeyCodec.chunk_key(chunk.object_id, chunk.chunk_id)
    wrong = make_chunk(object_id="object-B", chunk_id=chunk.chunk_id)
    db.put(ColumnFamily.CHUNKS, key, serialize({
        "object_id": wrong.object_id,
        "chunk_id": wrong.chunk_id,
        "content_hash": base64.b64encode(wrong.content_hash).decode("ascii"),
        "plaintext_size": wrong.plaintext_size,
        "created_at": wrong.created_at,
    }))

    with pytest.raises(CorruptMetadataError):
        ChunkRepository(db).get(chunk.object_id, chunk.chunk_id)


def test_chunk_repository_detects_invalid_base64(db):
    chunk = make_chunk()
    db.put(ColumnFamily.CHUNKS, KeyCodec.chunk_key(chunk.object_id, chunk.chunk_id), serialize({
        "object_id": chunk.object_id,
        "chunk_id": chunk.chunk_id,
        "content_hash": "not-base64!",
        "plaintext_size": chunk.plaintext_size,
        "created_at": chunk.created_at,
    }))

    with pytest.raises(CorruptMetadataError):
        ChunkRepository(db).get(chunk.object_id, chunk.chunk_id)


def test_chunk_and_dedup_index_are_atomically_staged(db):
    chunk_repo = ChunkRepository(db)
    dedup_repo = ChunkDedupIndexRepository(db)
    chunk = make_chunk()

    with db.batch() as batch:
        chunk_repo.put(chunk, batch)
        dedup_repo.put(
            chunk.object_id,
            chunk.content_hash,
            chunk.plaintext_size,
            chunk.chunk_id,
            batch,
        )

    assert chunk_repo.get(chunk.object_id, chunk.chunk_id) is None
    assert dedup_repo.get(
        chunk.object_id, chunk.content_hash, chunk.plaintext_size
    ) is None

    with db.batch() as batch:
        chunk_repo.put(chunk, batch)
        dedup_repo.put(
            chunk.object_id,
            chunk.content_hash,
            chunk.plaintext_size,
            chunk.chunk_id,
            batch,
        )
        batch.commit()

    indexed_id = dedup_repo.get(
        chunk.object_id, chunk.content_hash, chunk.plaintext_size
    )
    assert indexed_id == chunk.chunk_id
    assert chunk_repo.get(chunk.object_id, indexed_id) == chunk


def test_dedup_index_is_scoped_to_object(db):
    repo = ChunkDedupIndexRepository(db)
    chunk_a = make_chunk("object-A")
    chunk_b = make_chunk("object-B", content_hash=chunk_a.content_hash)

    with db.batch() as batch:
        repo.put(
            chunk_a.object_id, chunk_a.content_hash,
            chunk_a.plaintext_size, chunk_a.chunk_id, batch,
        )
        repo.put(
            chunk_b.object_id, chunk_b.content_hash,
            chunk_b.plaintext_size, chunk_b.chunk_id, batch,
        )
        batch.commit()

    assert repo.get("object-A", chunk_a.content_hash, chunk_a.plaintext_size) == chunk_a.chunk_id
    assert repo.get("object-B", chunk_b.content_hash, chunk_b.plaintext_size) == chunk_b.chunk_id


def test_dedup_index_rejects_duplicate_content_key(db):
    repo = ChunkDedupIndexRepository(db)
    chunk = make_chunk()
    with db.batch() as batch:
        repo.put(chunk.object_id, chunk.content_hash, chunk.plaintext_size, chunk.chunk_id, batch)
        batch.commit()

    with db.batch() as batch:
        with pytest.raises(RecordAlreadyExistsError):
            repo.put(
                chunk.object_id, chunk.content_hash, chunk.plaintext_size,
                str(uuid.uuid4()), batch,
            )


def test_dedup_index_rejects_invalid_lookup_arguments(db):
    repo = ChunkDedupIndexRepository(db)
    with pytest.raises(ValueError):
        repo.get("object-A", b"short", 10)
    with pytest.raises(ValueError):
        repo.get("object-A", b"h" * 32, True)
    with pytest.raises(ValueError):
        repo.get("object-A", b"h" * 32, 0)


def test_dedup_index_rejects_invalid_chunk_id(db):
    repo = ChunkDedupIndexRepository(db)
    with db.batch() as batch:
        with pytest.raises(ValueError):
            repo.put("object-A", b"h" * 32, 10, "not-a-uuid", batch)


def test_dedup_index_detects_corrupt_mapping(db):
    key = KeyCodec.chunk_dedup_key("object-A", b"h" * 32, 10)
    db.put(
        ColumnFamily.CHUNK_DEDUP_INDEX,
        key,
        serialize({"chunk_id": "not-a-uuid"}),
    )

    with pytest.raises(CorruptMetadataError):
        ChunkDedupIndexRepository(db).get("object-A", b"h" * 32, 10)


def test_dedup_lookup_requires_catalog_resolution(db):
    """A returned ID is not reusable until the caller resolves the Catalog."""
    chunk_repo = ChunkRepository(db)
    dedup_repo = ChunkDedupIndexRepository(db)
    missing_chunk_id = str(uuid.uuid4())

    with db.batch() as batch:
        dedup_repo.put("object-A", b"h" * 32, 10, missing_chunk_id, batch)
        batch.commit()

    indexed_id = dedup_repo.get("object-A", b"h" * 32, 10)
    assert indexed_id == missing_chunk_id
    assert chunk_repo.get("object-A", indexed_id) is None


def test_chunk_and_index_persist_after_database_reopen(tmp_path):
    path = tmp_path / "metadata.db"
    chunk = make_chunk()
    with MetadataDB(path) as db:
        with db.batch() as batch:
            ChunkRepository(db).put(chunk, batch)
            ChunkDedupIndexRepository(db).put(
                chunk.object_id, chunk.content_hash,
                chunk.plaintext_size, chunk.chunk_id, batch,
            )
            batch.commit()

    with MetadataDB(path) as reopened:
        assert ChunkRepository(reopened).get(chunk.object_id, chunk.chunk_id) == chunk
        assert ChunkDedupIndexRepository(reopened).get(
            chunk.object_id, chunk.content_hash, chunk.plaintext_size
        ) == chunk.chunk_id
