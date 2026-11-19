from uuid import uuid4

import pytest

from engram.metadata.physical.models import PlacementStatus, ShardPlacement


def make_placement(**overrides):
    values = {
        "placement_id": str(uuid4()),
        "generation_id": str(uuid4()),
        "shard_index": 0,
        "storage_node_id": "node-A",
        "storage_locator": "file:///storage/g1/shard-0",
        "status": PlacementStatus.PENDING,
        "created_at": 100,
        "updated_at": 100,
    }
    values.update(overrides)
    return ShardPlacement(**values)


def test_pending_shard_placement_is_valid():
    placement = make_placement()
    assert placement.status is PlacementStatus.PENDING


def test_stored_shard_placement_requires_stored_at():
    with pytest.raises(ValueError, match="stored_at"):
        make_placement(status=PlacementStatus.STORED)


def test_stored_shard_placement_accepts_stored_at():
    placement = make_placement(
        status=PlacementStatus.STORED,
        stored_at=110,
        updated_at=110,
    )
    assert placement.stored_at == 110


def test_stored_shard_placement_rejects_verified_at():
    with pytest.raises(ValueError, match="must not have verified_at"):
        make_placement(
            status=PlacementStatus.STORED,
            stored_at=110,
            verified_at=120,
            updated_at=120,
        )


def test_verified_shard_placement_requires_both_timestamps():
    with pytest.raises(ValueError, match="stored_at and verified_at"):
        make_placement(status=PlacementStatus.VERIFIED)


def test_verified_shard_placement_accepts_valid_timestamps():
    placement = make_placement(
        status=PlacementStatus.VERIFIED,
        stored_at=110,
        verified_at=120,
        updated_at=120,
    )
    assert placement.verified_at == 120


def test_lost_shard_placement_requires_prior_storage():
    with pytest.raises(ValueError, match="requires stored_at"):
        make_placement(status=PlacementStatus.LOST)


def test_failed_shard_placement_can_have_no_storage_timestamp():
    placement = make_placement(status=PlacementStatus.FAILED)
    assert placement.status is PlacementStatus.FAILED


def test_verified_at_requires_stored_at():
    with pytest.raises(ValueError, match="verified_at requires stored_at"):
        make_placement(verified_at=120, updated_at=120)


def test_updated_at_must_cover_lifecycle_timestamps():
    with pytest.raises(ValueError, match="updated_at"):
        make_placement(
            status=PlacementStatus.STORED,
            stored_at=110,
            updated_at=105,
        )


@pytest.mark.parametrize("field", ["placement_id", "generation_id"])
def test_shard_placement_rejects_invalid_uuids(field):
    with pytest.raises(ValueError):
        make_placement(**{field: "not-a-uuid"})


def test_shard_placement_rejects_negative_index():
    with pytest.raises(ValueError):
        make_placement(shard_index=-1)


def test_shard_placement_rejects_empty_node_or_locator():
    with pytest.raises(ValueError):
        make_placement(storage_node_id="")
    with pytest.raises(ValueError):
        make_placement(storage_locator="")


def test_shard_placement_rejects_invalid_status_type():
    with pytest.raises(TypeError):
        make_placement(status="pending")


def test_failed_placement_cannot_be_verified():
    with pytest.raises(ValueError, match="must not have verified_at"):
        make_placement(
            status=PlacementStatus.FAILED,
            stored_at=110,
            verified_at=120,
            updated_at=120,
        )
