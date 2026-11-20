from uuid import uuid4

import pytest

from engram.metadata.physical.models import (
    GenerationShard,
    PlacementStatus,
    ReceiptType,
    ShardPlacement,
    StorageReceipt,
)
from engram.metadata.physical.services import (
    StorageReceiptAlreadyExistsError,
    StorageReceiptIntegrityError,
    StorageReceiptNotFoundError,
    StorageReceiptPlacementNotFoundError,
    StorageReceiptService,
)


def uid():
    return str(uuid4())


class Placements:
    def __init__(self, values=()):
        self.values = {item.placement_id: item for item in values}

    def get(self, key):
        return self.values.get(key)


class Shards:
    def __init__(self, values=()):
        self.values = {(item.generation_id, item.shard_index): item for item in values}

    def get(self, generation_id, shard_index):
        return self.values.get((generation_id, shard_index))


class Receipts:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def list_for_placement(self, placement_id):
        return tuple(
            sorted(
                (
                    item
                    for item in self.values.values()
                    if item.placement_id == placement_id
                ),
                key=lambda item: (item.issued_at, item.receipt_id),
            )
        )

    def put(self, receipt, batch):
        if receipt.receipt_id in self.values:
            from engram.metadata.repositories import RecordAlreadyExistsError

            raise RecordAlreadyExistsError(receipt.receipt_id)
        batch.append(receipt)
        self.values[receipt.receipt_id] = receipt


@pytest.fixture
def setup():
    gid = uid()
    placement = ShardPlacement(
        uid(), gid, 2, "node-1", "disk://shard-2", PlacementStatus.PENDING, 100, 100
    )
    shard = GenerationShard(gid, 2, 4096, b"h" * 32)
    placements, shards, receipts = Placements([placement]), Shards([shard]), Receipts()
    service = StorageReceiptService(placements, shards, receipts)
    return service, placement, shard, receipts


def make_receipt(
    placement, shard, kind=ReceiptType.STORAGE_ACK, issued_at=110, **changes
):
    values = dict(
        receipt_id=uid(),
        placement_id=placement.placement_id,
        generation_id=placement.generation_id,
        shard_index=placement.shard_index,
        storage_node_id=placement.storage_node_id,
        receipt_type=kind,
        shard_hash=shard.shard_hash,
        stored_size=shard.shard_size,
        issued_at=issued_at,
        receipt_data=b"opaque-original-receipt",
    )
    values.update(changes)
    return StorageReceipt(**values)


def test_register_and_get_receipt(setup):
    service, placement, shard, receipts = setup
    receipt = make_receipt(placement, shard)
    batch = []
    assert service.register(receipt, batch=batch) == receipt
    assert batch == [receipt]
    assert service.get(receipt.receipt_id) == receipt
    assert service.require(receipt.receipt_id) == receipt


def test_register_rejects_missing_placement(setup):
    service, placement, shard, _ = setup
    orphan = make_receipt(placement, shard, placement_id=uid())
    with pytest.raises(StorageReceiptPlacementNotFoundError):
        service.register(orphan, batch=[])


@pytest.mark.parametrize(
    "field,value",
    [
        ("storage_node_id", "another-node"),
        ("shard_hash", b"x" * 32),
        ("stored_size", 1),
        ("generation_id", uid()),
        ("shard_index", 3),
    ],
)
def test_register_rejects_mismatched_metadata(setup, field, value):
    service, placement, shard, _ = setup
    receipt = make_receipt(placement, shard, **{field: value})
    with pytest.raises(StorageReceiptIntegrityError):
        service.register(receipt, batch=[])


def test_register_rejects_receipt_before_placement_creation(setup):
    service, placement, shard, _ = setup
    receipt = make_receipt(placement, shard, issued_at=99)
    with pytest.raises(StorageReceiptIntegrityError):
        service.register(receipt, batch=[])


def test_verification_receipt_cannot_predate_stored_at(setup):
    service, placement, shard, _ = setup
    stored = ShardPlacement(
        placement.placement_id,
        placement.generation_id,
        placement.shard_index,
        placement.storage_node_id,
        placement.storage_locator,
        PlacementStatus.STORED,
        placement.created_at,
        120,
        stored_at=120,
    )
    service._placements.values[placement.placement_id] = stored
    receipt = make_receipt(stored, shard, ReceiptType.STORAGE_VERIFICATION, 119)
    with pytest.raises(StorageReceiptIntegrityError):
        service.register(receipt, batch=[])


def test_list_and_validate_for_placement(setup):
    service, placement, shard, _ = setup
    receipt = make_receipt(placement, shard)
    service.register(receipt, batch=[])
    assert service.list_for_placement(placement.placement_id) == (receipt,)
    assert service.validate(receipt.receipt_id)
    assert service.validate_for_placement(placement.placement_id)


def test_validate_missing_receipt(setup):
    service, _, _, _ = setup
    with pytest.raises(StorageReceiptNotFoundError):
        service.validate(uid())


def test_receipt_is_append_only_and_duplicate_id_rejected(setup):
    service, placement, shard, _ = setup
    receipt = make_receipt(placement, shard)
    service.register(receipt, batch=[])
    with pytest.raises(StorageReceiptAlreadyExistsError):
        service.register(receipt, batch=[])


def test_list_for_missing_placement_rejected(setup):
    service, _, _, _ = setup
    with pytest.raises(StorageReceiptPlacementNotFoundError):
        service.list_for_placement(uid())
