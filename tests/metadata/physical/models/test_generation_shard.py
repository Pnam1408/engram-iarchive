from uuid import uuid4

import pytest

from engram.metadata.physical.models import GenerationShard


def make_shard(**overrides):
    values = {
        "generation_id": str(uuid4()),
        "shard_index": 0,
        "shard_size": 128,
        "shard_hash": b"h" * 32,
    }
    values.update(overrides)
    return GenerationShard(**values)


def test_generation_shard_accepts_valid_values():
    shard = make_shard()
    assert shard.shard_index == 0
    assert shard.shard_size == 128


@pytest.mark.parametrize("value", [-1, True, 1.5])
def test_generation_shard_rejects_invalid_index(value):
    with pytest.raises((TypeError, ValueError)):
        make_shard(shard_index=value)


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_generation_shard_rejects_invalid_size(value):
    with pytest.raises((TypeError, ValueError)):
        make_shard(shard_size=value)


def test_generation_shard_rejects_invalid_hash():
    with pytest.raises(ValueError):
        make_shard(shard_hash=b"short")


def test_generation_shard_rejects_non_uuid_generation_id():
    with pytest.raises(ValueError):
        make_shard(generation_id="generation-1")
