from uuid import uuid4

import pytest

from engram.metadata.physical.models import (
    Generation,
    GenerationShard,
    GenerationStatus,
    PlacementStatus,
    ReceiptType,
    ShardPlacement,
    StorageReceipt,
)
from engram.metadata.physical.services import (
    ShardAvailabilitySnapshot,
    ShardPlacementConcurrencyError,
    ShardPlacementIntegrityError,
    ShardPlacementPreconditionError,
    ShardPlacementService,
    StorageReceiptService,
)


def uid():
    return str(uuid4())


class Generations:
    def __init__(self, value):
        self.value = value

    def get(self, key):
        return self.value if self.value.generation_id == key else None


class Shards:
    def __init__(self, values):
        self.values = {(x.generation_id, x.shard_index): x for x in values}

    def get(self, generation_id, index):
        return self.values.get((generation_id, index))

    def list_for_generation(self, generation_id):
        return tuple(
            v for (g, _), v in sorted(self.values.items()) if g == generation_id
        )


class Placements:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def list_for_shard(self, generation_id, index):
        return tuple(
            v
            for v in self.values.values()
            if v.generation_id == generation_id and v.shard_index == index
        )

    def put(self, value, batch):
        batch.append(("put", value))
        self.values[value.placement_id] = value

    def update(self, value, batch):
        batch.append(("update", value))
        self.values[value.placement_id] = value


class Receipts:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return next(
            (
                item
                for items in self.values.values()
                for item in items
                if item.receipt_id == key
            ),
            None,
        )

    def put(self, value, batch):
        batch.append(("receipt", value))
        self.values.setdefault(value.placement_id, []).append(value)

    def list_for_placement(self, key):
        return tuple(self.values.get(key, ()))


@pytest.fixture
def setup():
    gid = uid()
    generation = Generation(
        gid, 2, 3, GenerationStatus.STORING, 10, 12, sealed_at=11, encoded_at=12
    )
    shards = [GenerationShard(gid, i, 128, bytes([i + 1]) * 32) for i in range(3)]
    placements, receipts = Placements(), Receipts()
    shard_repository = Shards(shards)
    receipt_service = StorageReceiptService(placements, shard_repository, receipts)
    service = ShardPlacementService(
        Generations(generation),
        shard_repository,
        placements,
        receipt_service,
        clock=lambda: 100,
    )
    placement = ShardPlacement(
        uid(), gid, 0, "node-a", "loc-0", PlacementStatus.PENDING, 20, 20
    )
    return service, placement, shards[0], placements, receipts


def receipt(placement, shard, kind, issued_at):
    return StorageReceipt(
        uid(),
        placement.placement_id,
        placement.generation_id,
        placement.shard_index,
        placement.storage_node_id,
        kind,
        shard.shard_hash,
        shard.shard_size,
        issued_at,
        b"opaque",
    )


def test_register_pending_placement(setup):
    service, placement, _, placements, _ = setup
    batch = []
    assert service.register(placement, batch=batch) == placement
    assert placements.get(placement.placement_id) == placement
    assert batch[0][0] == "put"


def test_register_requires_storing_generation(setup):
    service, placement, _, _, _ = setup
    service._generations.value = Generation(
        placement.generation_id,
        2,
        3,
        GenerationStatus.ENCODED,
        10,
        12,
        sealed_at=11,
        encoded_at=12,
    )
    with pytest.raises(ShardPlacementPreconditionError):
        service.register(placement, batch=[])


def test_mark_stored_records_ack_and_timestamp(setup):
    service, placement, shard, placements, receipts = setup
    service.register(placement, batch=[])
    ack = receipt(placement, shard, ReceiptType.STORAGE_ACK, 30)
    updated = service.mark_stored(placement.placement_id, ack, 20, batch=[])
    assert updated.status is PlacementStatus.STORED
    assert updated.stored_at == 30
    assert receipts.list_for_placement(placement.placement_id) == (ack,)
    assert placements.get(placement.placement_id) == updated


def test_mark_verified_requires_persisted_ack_and_records_verification(setup):
    service, placement, shard, _, receipts = setup
    service.register(placement, batch=[])
    ack = receipt(placement, shard, ReceiptType.STORAGE_ACK, 30)
    stored = service.mark_stored(placement.placement_id, ack, 20, batch=[])
    verification = receipt(stored, shard, ReceiptType.STORAGE_VERIFICATION, 40)
    verified = service.mark_verified(
        placement.placement_id, verification, stored.updated_at, batch=[]
    )
    assert verified.status is PlacementStatus.VERIFIED
    assert verified.verified_at == 40
    assert len(receipts.list_for_placement(placement.placement_id)) == 2


def test_mark_verified_rejects_missing_ack(setup):
    service, placement, shard, _, _ = setup
    service.register(placement, batch=[])
    service._placements.values[placement.placement_id] = ShardPlacement(
        placement.placement_id,
        placement.generation_id,
        0,
        placement.storage_node_id,
        placement.storage_locator,
        PlacementStatus.STORED,
        20,
        30,
        stored_at=30,
    )
    verification = receipt(placement, shard, ReceiptType.STORAGE_VERIFICATION, 40)
    with pytest.raises(ShardPlacementIntegrityError):
        service.mark_verified(placement.placement_id, verification, 30, batch=[])


def test_stale_update_is_rejected(setup):
    service, placement, shard, _, _ = setup
    service.register(placement, batch=[])
    ack = receipt(placement, shard, ReceiptType.STORAGE_ACK, 30)
    with pytest.raises(ShardPlacementConcurrencyError):
        service.mark_stored(placement.placement_id, ack, 19, batch=[])


def test_receipt_must_match_shard_hash(setup):
    service, placement, shard, _, _ = setup
    service.register(placement, batch=[])
    bad = StorageReceipt(
        uid(),
        placement.placement_id,
        placement.generation_id,
        0,
        placement.storage_node_id,
        ReceiptType.STORAGE_ACK,
        b"x" * 32,
        shard.shard_size,
        30,
        b"opaque",
    )
    with pytest.raises(ShardPlacementIntegrityError):
        service.mark_stored(placement.placement_id, bad, 20, batch=[])


def test_availability_counts_shard_indices_not_placements(setup):
    service, placement, shard, _, _ = setup
    service.register(placement, batch=[])
    ack = receipt(placement, shard, ReceiptType.STORAGE_ACK, 30)
    stored = service.mark_stored(placement.placement_id, ack, 20, batch=[])
    verification = receipt(stored, shard, ReceiptType.STORAGE_VERIFICATION, 40)
    service.mark_verified(placement.placement_id, verification, stored.updated_at, batch=[])
    assert service.get_availability_snapshot(
        placement.generation_id
    ) == ShardAvailabilitySnapshot(placement.generation_id, frozenset({0}))


def test_failed_and_lost_transitions(setup):
    service, placement, shard, _, _ = setup
    service.register(placement, batch=[])
    failed = service.mark_failed(placement.placement_id, 20, batch=[])
    assert failed.status is PlacementStatus.FAILED
    with pytest.raises(ShardPlacementPreconditionError):
        service.mark_lost(placement.placement_id, failed.updated_at, batch=[])


def test_validate_verified_placement(setup):
    service, placement, shard, _, _ = setup
    service.register(placement, batch=[])
    ack = receipt(placement, shard, ReceiptType.STORAGE_ACK, 30)
    stored = service.mark_stored(placement.placement_id, ack, 20, batch=[])
    verification = receipt(stored, shard, ReceiptType.STORAGE_VERIFICATION, 40)
    service.mark_verified(placement.placement_id, verification, stored.updated_at, batch=[])
    assert service.validate(placement.placement_id)


def test_mark_stored_rejects_verification_receipt(setup):
    service, placement, shard, _, receipts = setup
    service.register(placement, batch=[])
    wrong_type = receipt(placement, shard, ReceiptType.STORAGE_VERIFICATION, 30)
    with pytest.raises(ShardPlacementPreconditionError, match="STORAGE_ACK"):
        service.mark_stored(placement.placement_id, wrong_type, 20, batch=[])
    assert receipts.list_for_placement(placement.placement_id) == ()


def test_mark_verified_rejects_ack_receipt(setup):
    service, placement, shard, _, receipts = setup
    service.register(placement, batch=[])
    ack = receipt(placement, shard, ReceiptType.STORAGE_ACK, 30)
    stored = service.mark_stored(placement.placement_id, ack, 20, batch=[])
    wrong_type = receipt(stored, shard, ReceiptType.STORAGE_ACK, 40)
    with pytest.raises(ShardPlacementPreconditionError, match="STORAGE_VERIFICATION"):
        service.mark_verified(placement.placement_id, wrong_type, stored.updated_at, batch=[])
    assert len(receipts.list_for_placement(placement.placement_id)) == 1


def test_availability_rejects_verified_placement_without_receipt_evidence(setup):
    service, placement, shard, placements, receipts = setup
    service.register(placement, batch=[])
    ack = receipt(placement, shard, ReceiptType.STORAGE_ACK, 30)
    stored = service.mark_stored(placement.placement_id, ack, 20, batch=[])
    verification = receipt(stored, shard, ReceiptType.STORAGE_VERIFICATION, 40)
    verified = service.mark_verified(
        placement.placement_id, verification, stored.updated_at, batch=[]
    )

    # Simulate corrupt/incomplete persisted metadata: the status remains VERIFIED,
    # but its required verification receipt is missing.
    receipts.values[placement.placement_id] = [ack]
    with pytest.raises(ShardPlacementIntegrityError, match="STORAGE_VERIFICATION"):
        service.get_availability_snapshot(placement.generation_id)
