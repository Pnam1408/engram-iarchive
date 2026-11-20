import uuid

import pytest

from engram.metadata.database import ColumnFamily, KeyCodec
from engram.metadata.database.serialization import serialize
from engram.metadata.physical.models import (
    ChunkPlacement,
    PlacementStatus,
    ShardPlacement,
)
from engram.metadata.physical.repositories import (
    ChunkPlacementRepository,
    ShardPlacementRepository,
)
from engram.metadata.repositories import (
    CorruptMetadataError,
    RecordAlreadyExistsError,
)


class MemoryDB:
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
        return iter(sorted(
            (key, value)
            for (cf, key), value in self._records.items()
            if cf == name and key.startswith(prefix)
        ))


class MemoryBatch:
    def __init__(self, db):
        self.db = db
        self.operations = []

    def put(self, column_family, key, value):
        self.operations.append((column_family, key, value))

    def commit(self):
        for cf, key, value in self.operations:
            self.db.put(cf, key, value)


def uid():
    return str(uuid.uuid4())


def make_chunk_placement(*, placement_id=None, chunk_id=None, generation_id=None):
    return ChunkPlacement(
        placement_id=placement_id or uid(),
        object_id="object-A",
        chunk_id=chunk_id or uid(),
        generation_id=generation_id or uid(),
        record_id=uid(),
        created_at=10,
    )


def make_shard_placement(
    *,
    placement_id=None,
    status=PlacementStatus.PENDING,
    updated_at=10,
    stored_at=None,
    verified_at=None,
    generation_id=None,
    shard_index=0,
    storage_node_id="node-A",
    storage_locator="disk://node-A/shard-0",
):
    return ShardPlacement(
        placement_id=placement_id or uid(),
        generation_id=generation_id or uid(),
        shard_index=shard_index,
        storage_node_id=storage_node_id,
        storage_locator=storage_locator,
        status=status,
        created_at=1,
        updated_at=updated_at,
        stored_at=stored_at,
        verified_at=verified_at,
    )


def test_chunk_placement_round_trip_and_multiple_placements_for_chunk():
    db = MemoryDB()
    repo = ChunkPlacementRepository(db)
    chunk_id = uid()
    first = make_chunk_placement(chunk_id=chunk_id)
    second = make_chunk_placement(chunk_id=chunk_id)

    batch = MemoryBatch(db)
    repo.put(first, batch)
    repo.put(second, batch)
    batch.commit()

    assert repo.get(first.placement_id) == first
    assert repo.list_for_chunk("object-A", chunk_id) == tuple(
        sorted((first, second), key=lambda item: item.placement_id)
    )


def test_chunk_placement_rejects_duplicate_primary_id():
    db = MemoryDB()
    repo = ChunkPlacementRepository(db)
    placement = make_chunk_placement()
    batch = MemoryBatch(db)
    repo.put(placement, batch)
    batch.commit()

    with pytest.raises(RecordAlreadyExistsError):
        repo.put(placement, MemoryBatch(db))


def test_chunk_placement_detects_dangling_secondary_index():
    db = MemoryDB()
    repo = ChunkPlacementRepository(db)
    placement = make_chunk_placement()
    db.put(
        ColumnFamily.CHUNK_PLACEMENT_INDEX,
        KeyCodec.chunk_placement_index_key(
            placement.object_id, placement.chunk_id, placement.placement_id
        ),
        serialize({"placement_id": placement.placement_id}),
    )

    with pytest.raises(CorruptMetadataError, match="missing primary"):
        repo.list_for_chunk(placement.object_id, placement.chunk_id)


def test_chunk_placement_detects_index_value_mismatch():
    db = MemoryDB()
    repo = ChunkPlacementRepository(db)
    placement = make_chunk_placement()
    db.put(
        ColumnFamily.CHUNK_PLACEMENTS,
        KeyCodec.chunk_placement_key(placement.placement_id),
        serialize(repo._to_dict(placement)),
    )
    db.put(
        ColumnFamily.CHUNK_PLACEMENT_INDEX,
        KeyCodec.chunk_placement_index_key(
            placement.object_id, placement.chunk_id, placement.placement_id
        ),
        serialize({"placement_id": uid()}),
    )

    with pytest.raises(CorruptMetadataError, match="index value"):
        repo.list_for_chunk(placement.object_id, placement.chunk_id)


def test_shard_placement_round_trip_and_index_lookup():
    db = MemoryDB()
    repo = ShardPlacementRepository(db)
    generation_id = uid()
    first = make_shard_placement(generation_id=generation_id)
    second = make_shard_placement(generation_id=generation_id)

    batch = MemoryBatch(db)
    repo.put(first, batch)
    repo.put(second, batch)
    batch.commit()

    assert repo.get(first.placement_id) == first
    assert repo.list_for_shard(generation_id, 0) == tuple(
        sorted((first, second), key=lambda item: item.placement_id)
    )


def test_shard_placement_lifecycle_update_preserves_identity_and_index():
    db = MemoryDB()
    repo = ShardPlacementRepository(db)
    pending = make_shard_placement()
    batch = MemoryBatch(db)
    repo.put(pending, batch)
    batch.commit()

    stored = make_shard_placement(
        placement_id=pending.placement_id,
        generation_id=pending.generation_id,
        status=PlacementStatus.STORED,
        updated_at=12,
        stored_at=11,
        storage_node_id=pending.storage_node_id,
        storage_locator=pending.storage_locator,
    )
    batch = MemoryBatch(db)
    repo.update(stored, batch)
    batch.commit()

    assert repo.get(pending.placement_id) == stored
    assert repo.list_for_shard(pending.generation_id, 0) == (stored,)


def test_shard_placement_rejects_identity_mutation():
    db = MemoryDB()
    repo = ShardPlacementRepository(db)
    pending = make_shard_placement()
    batch = MemoryBatch(db)
    repo.put(pending, batch)
    batch.commit()

    changed = make_shard_placement(
        placement_id=pending.placement_id,
        generation_id=pending.generation_id,
        storage_node_id="node-B",
        storage_locator=pending.storage_locator,
    )
    with pytest.raises(ValueError, match="storage_node_id is immutable"):
        repo.update(changed, MemoryBatch(db))


def test_shard_placement_rejects_timestamp_regression():
    db = MemoryDB()
    repo = ShardPlacementRepository(db)
    pending = make_shard_placement(updated_at=10)
    batch = MemoryBatch(db)
    repo.put(pending, batch)
    batch.commit()

    stale = make_shard_placement(
        placement_id=pending.placement_id,
        generation_id=pending.generation_id,
        updated_at=9,
    )
    with pytest.raises(ValueError, match="must not move backwards"):
        repo.update(stale, MemoryBatch(db))


def test_shard_placement_requires_existing_record_for_update():
    db = MemoryDB()
    repo = ShardPlacementRepository(db)
    with pytest.raises(KeyError, match="does not exist"):
        repo.update(make_shard_placement(), MemoryBatch(db))


def test_shard_placement_detects_missing_secondary_index_on_update():
    db = MemoryDB()
    repo = ShardPlacementRepository(db)
    placement = make_shard_placement()
    db.put(
        ColumnFamily.SHARD_PLACEMENTS,
        KeyCodec.shard_placement_key(placement.placement_id),
        serialize(repo._to_dict(placement)),
    )

    updated = make_shard_placement(
        placement_id=placement.placement_id,
        generation_id=placement.generation_id,
        updated_at=11,
    )
    with pytest.raises(CorruptMetadataError, match="index is missing"):
        repo.update(updated, MemoryBatch(db))
