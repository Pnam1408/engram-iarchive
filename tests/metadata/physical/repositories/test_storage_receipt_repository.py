import uuid

import pytest

from engram.metadata.database import ColumnFamily, KeyCodec
from engram.metadata.database.serialization import serialize
from engram.metadata.physical.models import ReceiptType, StorageReceipt
from engram.metadata.physical.repositories import StorageReceiptRepository
from engram.metadata.repositories import CorruptMetadataError, RecordAlreadyExistsError


class MemoryDB:
    def __init__(self):
        self.records = {}

    def get(self, cf, key):
        return self.records.get((cf, key))

    def put(self, cf, key, value):
        self.records[(cf, key)] = value

    def iter_prefix(self, cf, prefix):
        return iter(sorted((key, value) for (family, key), value in self.records.items()
                           if family == cf and key.startswith(prefix)))


class MemoryBatch:
    def __init__(self, db):
        self.db = db
        self.operations = []

    def put(self, cf, key, value):
        self.operations.append((cf, key, value))

    def commit(self):
        for cf, key, value in self.operations:
            self.db.put(cf, key, value)


def uid():
    return str(uuid.uuid4())


def make_receipt(*, receipt_id=None, placement_id=None, issued_at=100):
    return StorageReceipt(
        receipt_id=receipt_id or uid(),
        placement_id=placement_id or uid(),
        generation_id=uid(),
        shard_index=2,
        storage_node_id="node-A",
        receipt_type=ReceiptType.STORAGE_ACK,
        shard_hash=b"h" * 32,
        stored_size=4096,
        issued_at=issued_at,
        receipt_data=b"opaque-signed-receipt",
    )


def test_storage_receipt_round_trip_and_ordered_placement_lookup():
    db = MemoryDB()
    repo = StorageReceiptRepository(db)
    placement_id = uid()
    later = make_receipt(placement_id=placement_id, issued_at=200)
    first = make_receipt(placement_id=placement_id, issued_at=100)
    same_time = make_receipt(placement_id=placement_id, issued_at=100)

    batch = MemoryBatch(db)
    for item in (later, first, same_time):
        repo.put(item, batch)
    batch.commit()

    assert repo.get(first.receipt_id) == first
    assert repo.list_for_placement(placement_id) == tuple(
        sorted((later, first, same_time), key=lambda item: (item.issued_at, item.receipt_id))
    )


def test_storage_receipt_rejects_duplicate_id():
    db = MemoryDB()
    repo = StorageReceiptRepository(db)
    receipt = make_receipt()
    batch = MemoryBatch(db)
    repo.put(receipt, batch)
    batch.commit()

    with pytest.raises(RecordAlreadyExistsError):
        repo.put(receipt, MemoryBatch(db))


def test_storage_receipt_detects_dangling_index():
    db = MemoryDB()
    repo = StorageReceiptRepository(db)
    receipt = make_receipt()
    db.put(
        ColumnFamily.STORAGE_RECEIPT_INDEX,
        KeyCodec.storage_receipt_index_key(receipt.placement_id, receipt.issued_at, receipt.receipt_id),
        serialize({"placement_id": receipt.placement_id, "issued_at": receipt.issued_at,
                  "receipt_id": receipt.receipt_id}),
    )
    with pytest.raises(CorruptMetadataError, match="missing primary"):
        repo.list_for_placement(receipt.placement_id)


def test_storage_receipt_detects_index_value_mismatch():
    db = MemoryDB()
    repo = StorageReceiptRepository(db)
    receipt = make_receipt()
    db.put(ColumnFamily.STORAGE_RECEIPTS, KeyCodec.storage_receipt_key(receipt.receipt_id),
           serialize(repo._to_dict(receipt)))
    db.put(
        ColumnFamily.STORAGE_RECEIPT_INDEX,
        KeyCodec.storage_receipt_index_key(receipt.placement_id, receipt.issued_at, receipt.receipt_id),
        serialize({"placement_id": receipt.placement_id, "issued_at": receipt.issued_at,
                   "receipt_id": uid()}),
    )
    with pytest.raises(CorruptMetadataError, match="index value"):
        repo.list_for_placement(receipt.placement_id)


def test_storage_receipt_is_immutable_and_requires_batch():
    db = MemoryDB()
    repo = StorageReceiptRepository(db)
    receipt = make_receipt()
    with pytest.raises(TypeError, match="batch is required"):
        repo.put(receipt, None)
