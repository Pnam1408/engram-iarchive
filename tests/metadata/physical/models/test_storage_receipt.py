from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest

from engram.metadata.physical.models import ReceiptType, StorageReceipt


def make_receipt(**overrides):
    values = {
        "receipt_id": str(uuid4()),
        "placement_id": str(uuid4()),
        "generation_id": str(uuid4()),
        "shard_index": 0,
        "storage_node_id": "node-01",
        "receipt_type": ReceiptType.STORAGE_ACK,
        "shard_hash": b"h" * 32,
        "stored_size": 1024,
        "issued_at": 1_700_000_000_000,
        "receipt_data": b"opaque-receipt-payload",
    }
    values.update(overrides)
    return StorageReceipt(**values)


def test_storage_receipt_round_trip_fields():
    receipt = make_receipt()

    assert receipt.receipt_type is ReceiptType.STORAGE_ACK
    assert receipt.shard_index == 0
    assert receipt.stored_size == 1024
    assert receipt.receipt_data == b"opaque-receipt-payload"


def test_storage_verification_receipt_is_supported():
    receipt = make_receipt(receipt_type=ReceiptType.STORAGE_VERIFICATION)

    assert receipt.receipt_type is ReceiptType.STORAGE_VERIFICATION


@pytest.mark.parametrize("field", ["receipt_id", "placement_id", "generation_id"])
def test_rejects_invalid_uuid(field):
    with pytest.raises(ValueError):
        make_receipt(**{field: "not-a-uuid"})


def test_rejects_non_v4_uuid():
    with pytest.raises(ValueError):
        make_receipt(receipt_id="00000000-0000-0000-0000-000000000000")


@pytest.mark.parametrize("value", [-1, True, 1.5, "0"])
def test_rejects_invalid_shard_index(value):
    with pytest.raises((TypeError, ValueError)):
        make_receipt(shard_index=value)


@pytest.mark.parametrize("value", ["", None, 1])
def test_rejects_invalid_storage_node_id(value):
    with pytest.raises((TypeError, ValueError)):
        make_receipt(storage_node_id=value)


def test_rejects_invalid_receipt_type():
    with pytest.raises(TypeError):
        make_receipt(receipt_type="storage_ack")


def test_rejects_invalid_shard_hash_type():
    with pytest.raises(TypeError):
        make_receipt(shard_hash="h" * 32)


def test_rejects_wrong_shard_hash_length():
    with pytest.raises(ValueError):
        make_receipt(shard_hash=b"h" * 31)


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_rejects_invalid_stored_size(value):
    with pytest.raises((TypeError, ValueError)):
        make_receipt(stored_size=value)


@pytest.mark.parametrize("value", [-1, True, 1.5, "1"])
def test_rejects_invalid_issued_at(value):
    with pytest.raises((TypeError, ValueError)):
        make_receipt(issued_at=value)


@pytest.mark.parametrize("value", [b"", None, "payload"])
def test_rejects_invalid_receipt_data(value):
    with pytest.raises((TypeError, ValueError)):
        make_receipt(receipt_data=value)


def test_receipt_is_immutable():
    receipt = make_receipt()

    with pytest.raises(FrozenInstanceError):
        receipt.stored_size = 2048
