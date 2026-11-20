import uuid

import pytest

from engram.metadata.database import ColumnFamily, KeyCodec
from engram.metadata.database.serialization import serialize
from engram.metadata.physical.models import (
    Generation,
    GenerationManifest,
    GenerationManifestEntry,
    GenerationShard,
    GenerationStatus,
)
from engram.metadata.physical.repositories import (
    GenerationManifestEntryRepository,
    GenerationManifestRepository,
    GenerationRepository,
    GenerationShardRepository,
)
from engram.metadata.repositories import (
    CorruptMetadataError,
    RecordAlreadyExistsError,
)


class MemoryDB:
    """Small MetadataDB-compatible store for repository unit tests."""

    def __init__(self):
        self._records = {}

    @staticmethod
    def _cf_name(column_family):
        return column_family.value if hasattr(column_family, "value") else column_family

    def get(self, column_family, key):
        return self._records.get((self._cf_name(column_family), key))

    def put(self, column_family, key, value):
        self._records[(self._cf_name(column_family), key)] = value

    def iter_prefix(self, column_family, prefix):
        name = self._cf_name(column_family)
        return iter(
            sorted(
                (key, value)
                for (cf, key), value in self._records.items()
                if cf == name and key.startswith(prefix)
            )
        )


class MemoryBatch:
    def __init__(self, db):
        self.db = db
        self.operations = []

    def put(self, column_family, key, value):
        self.operations.append((column_family, key, value))

    def commit(self):
        for column_family, key, value in self.operations:
            self.db.put(column_family, key, value)


def _uuid():
    return str(uuid.uuid4())


def _generation(
    generation_id=None,
    *,
    status=GenerationStatus.OPEN,
    updated_at=10,
    sealed_at=None,
    encoded_at=None,
    data_shards=2,
):
    return Generation(
        generation_id=generation_id or _uuid(),
        data_shards=data_shards,
        total_shards=4,
        status=status,
        created_at=1,
        updated_at=updated_at,
        sealed_at=sealed_at,
        encoded_at=encoded_at,
    )


def _entry(generation_id, offset, length):
    return GenerationManifestEntry(
        generation_id=generation_id,
        record_id=_uuid(),
        object_id="object-A",
        chunk_id=_uuid(),
        offset=offset,
        length=length,
        record_hash=b"r" * 32,
    )


def test_generation_round_trip_and_lifecycle_update():
    db = MemoryDB()
    repository = GenerationRepository(db)
    generation = _generation()

    batch = MemoryBatch(db)
    repository.put(generation, batch)
    batch.commit()

    assert repository.get(generation.generation_id) == generation
    assert repository.exists(generation.generation_id)

    sealed = _generation(
        generation.generation_id,
        status=GenerationStatus.SEALED,
        updated_at=11,
        sealed_at=11,
    )
    batch = MemoryBatch(db)
    repository.put(sealed, batch)
    batch.commit()
    assert repository.get(generation.generation_id) == sealed


def test_generation_rejects_immutable_configuration_change():
    db = MemoryDB()
    repository = GenerationRepository(db)
    generation = _generation()
    batch = MemoryBatch(db)
    repository.put(generation, batch)
    batch.commit()

    changed = _generation(generation.generation_id, data_shards=3)
    with pytest.raises(ValueError, match="data_shards is immutable"):
        repository.put(changed, MemoryBatch(db))


def test_generation_rejects_updated_at_regression():
    db = MemoryDB()
    repository = GenerationRepository(db)
    generation = _generation()
    batch = MemoryBatch(db)
    repository.put(generation, batch)
    batch.commit()

    stale = _generation(generation.generation_id, updated_at=9)
    with pytest.raises(ValueError, match="must not move backwards"):
        repository.put(stale, MemoryBatch(db))


def test_manifest_round_trip_and_immutable_duplicate_rejection():
    db = MemoryDB()
    repository = GenerationManifestRepository(db)
    manifest = GenerationManifest(_uuid(), 1, 5, b"m" * 32, 10)

    batch = MemoryBatch(db)
    repository.put(manifest, batch)
    batch.commit()

    assert repository.get(manifest.generation_id) == manifest
    with pytest.raises(RecordAlreadyExistsError):
        repository.put(manifest, MemoryBatch(db))


def test_manifest_entry_round_trip_in_offset_order():
    db = MemoryDB()
    repository = GenerationManifestEntryRepository(db)
    generation_id = _uuid()
    first = _entry(generation_id, 0, 3)
    second = _entry(generation_id, 3, 2)
    manifest = GenerationManifest(generation_id, 2, 5, b"m" * 32, 10)

    batch = MemoryBatch(db)
    repository.put_all(manifest, (first, second), batch)
    batch.commit()

    assert repository.get(generation_id, 3) == second
    assert repository.list_for_generation(generation_id) == (first, second)


def test_manifest_entry_rejects_non_contiguous_offsets():
    db = MemoryDB()
    repository = GenerationManifestEntryRepository(db)
    generation_id = _uuid()
    entry = _entry(generation_id, 1, 2)
    manifest = GenerationManifest(generation_id, 1, 2, b"m" * 32, 10)

    with pytest.raises(ValueError, match="contiguous"):
        repository.put_all(manifest, (entry,), MemoryBatch(db))


def test_manifest_entry_rejects_second_write_for_same_generation():
    db = MemoryDB()
    repository = GenerationManifestEntryRepository(db)
    generation_id = _uuid()
    entry = _entry(generation_id, 0, 2)
    manifest = GenerationManifest(generation_id, 1, 2, b"m" * 32, 10)

    batch = MemoryBatch(db)
    repository.put_all(manifest, (entry,), batch)
    batch.commit()

    with pytest.raises(RecordAlreadyExistsError):
        repository.put_all(manifest, (entry,), MemoryBatch(db))


def test_generation_shard_round_trip_and_index_order():
    db = MemoryDB()
    repository = GenerationShardRepository(db)
    generation_id = _uuid()
    shard_1 = GenerationShard(generation_id, 1, 100, b"a" * 32)
    shard_0 = GenerationShard(generation_id, 0, 100, b"b" * 32)

    batch = MemoryBatch(db)
    repository.put(shard_1, batch)
    repository.put(shard_0, batch)
    batch.commit()

    assert repository.get(generation_id, 1) == shard_1
    assert repository.list_for_generation(generation_id) == (shard_0, shard_1)
    with pytest.raises(RecordAlreadyExistsError):
        repository.put(shard_1, MemoryBatch(db))


def test_manifest_repository_detects_value_key_mismatch():
    db = MemoryDB()
    repository = GenerationManifestRepository(db)
    key_generation_id = _uuid()
    value_generation_id = _uuid()
    wrong_manifest = GenerationManifest(value_generation_id, 1, 1, b"x" * 32, 1)
    db.put(
        ColumnFamily.GENERATION_MANIFESTS,
        KeyCodec.generation_manifest_key(key_generation_id),
        serialize(repository._to_dict(wrong_manifest)),
    )

    with pytest.raises(CorruptMetadataError):
        repository.get(key_generation_id)


def test_manifest_entry_has_no_version_binding_and_round_trips_without_it():
    db = MemoryDB()
    repository = GenerationManifestEntryRepository(db)
    generation_id = _uuid()
    entry = _entry(generation_id, 0, 2)

    encoded = repository._to_dict(entry)
    assert "version_id" not in encoded
    assert repository._from_dict(encoded) == entry

    with pytest.raises(TypeError):
        GenerationManifestEntry(
            generation_id=generation_id,
            record_id=_uuid(),
            object_id="object-A",
            version_id=_uuid(),
            chunk_id=_uuid(),
            offset=0,
            length=2,
            record_hash=b"r" * 32,
        )
