from uuid import uuid4

import pytest

from engram.metadata.physical.models import ChunkPlacement


def make_placement(**overrides):
    values = {
        "placement_id": str(uuid4()),
        "object_id": "object-A",
        "chunk_id": str(uuid4()),
        "generation_id": str(uuid4()),
        "record_id": str(uuid4()),
        "created_at": 100,
    }
    values.update(overrides)
    return ChunkPlacement(**values)


def test_chunk_placement_accepts_valid_values():
    placement = make_placement()
    assert placement.object_id == "object-A"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("placement_id", "not-a-uuid"),
        ("object_id", ""),
        ("chunk_id", "not-a-uuid"),
        ("generation_id", "not-a-uuid"),
        ("record_id", "not-a-uuid"),
        ("created_at", -1),
    ],
)
def test_chunk_placement_rejects_invalid_fields(field, value):
    with pytest.raises((TypeError, ValueError)):
        make_placement(**{field: value})
