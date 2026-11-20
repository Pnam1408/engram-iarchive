import uuid

import pytest

from engram.metadata.physical.models import Generation, GenerationShard, GenerationStatus
from engram.metadata.physical.services import (
    GenerationShardAlreadyRegisteredError,
    GenerationShardGenerationNotFoundError,
    GenerationShardIntegrityError,
    GenerationShardNotFoundError,
    GenerationShardPreconditionError,
    GenerationShardService,
    GenerationShardSet,
)


class Batch:
    pass


class Generations:
    def __init__(self):
        self.items = {}

    def get(self, key):
        return self.items.get(key)


class Shards:
    def __init__(self):
        self.items = {}
        self.staged = []

    def get(self, generation_id, shard_index):
        return self.items.get((generation_id, shard_index))

    def list_for_generation(self, generation_id):
        return tuple(
            self.items[key]
            for key in sorted(self.items)
            if key[0] == generation_id
        )

    def put(self, shard, batch):
        self.staged.append((shard, batch))


@pytest.fixture
def setup():
    generations, shards = Generations(), Shards()
    service = GenerationShardService(generations, shards)
    gid = str(uuid.uuid4())
    generations.items[gid] = Generation(
        gid, 2, 4, GenerationStatus.ENCODING, 10, 12, sealed_at=11
    )
    records = tuple(
        GenerationShard(gid, i, 100 + i, bytes([i]) * 32)
        for i in range(4)
    )
    return service, generations, shards, gid, records


def test_register_stages_complete_shard_set_in_index_order(setup):
    service, _, repository, gid, records = setup
    batch = Batch()
    result = service.register_shards(gid, tuple(reversed(records)), batch)

    assert result == GenerationShardSet(gid, records)
    assert repository.staged == [(record, batch) for record in records]


def test_register_requires_existing_generation(setup):
    service, _, _, _, records = setup
    with pytest.raises(GenerationShardGenerationNotFoundError):
        service.register_shards(str(uuid.uuid4()), records, Batch())


def test_register_only_during_encoding(setup):
    service, generations, repository, gid, records = setup
    generations.items[gid] = Generation(
        gid, 2, 4, GenerationStatus.SEALED, 10, 11, sealed_at=11
    )
    with pytest.raises(GenerationShardPreconditionError):
        service.register_shards(gid, records, Batch())
    assert not repository.staged


@pytest.mark.parametrize("invalid", [
    "not-a-tuple",
    (GenerationShard(str(uuid.uuid4()), 0, 1, b"x" * 32),),
])
def test_register_rejects_invalid_container_or_wrong_generation(setup, invalid):
    service, _, repository, gid, _ = setup
    with pytest.raises((TypeError, GenerationShardPreconditionError)):
        service.register_shards(gid, invalid, Batch())
    assert not repository.staged


def test_register_rejects_missing_or_duplicate_indices(setup):
    service, _, repository, gid, records = setup
    with pytest.raises(GenerationShardPreconditionError):
        service.register_shards(gid, records[:-1], Batch())
    duplicated = records[:-1] + (records[0],)
    with pytest.raises(GenerationShardPreconditionError):
        service.register_shards(gid, duplicated, Batch())
    assert not repository.staged


def test_register_rejects_existing_partial_shard_set(setup):
    service, _, repository, gid, records = setup
    repository.items[(gid, 0)] = records[0]
    with pytest.raises(GenerationShardAlreadyRegisteredError):
        service.register_shards(gid, records, Batch())
    assert not repository.staged


def test_getters_and_validate_complete_set(setup):
    service, _, repository, gid, records = setup
    repository.items.update({(gid, shard.shard_index): shard for shard in records})

    assert service.get_shard(gid, 2) == records[2]
    assert service.list_shards(gid) == records
    assert service.get_shard_set(gid) == GenerationShardSet(gid, records)
    assert service.validate(gid) is True


def test_missing_and_incomplete_persisted_sets(setup):
    service, _, repository, gid, records = setup
    assert service.get_shard_set(gid) is None
    with pytest.raises(GenerationShardNotFoundError):
        service.validate(gid)

    repository.items[(gid, 0)] = records[0]
    with pytest.raises(GenerationShardIntegrityError):
        service.get_shard_set(gid)
    with pytest.raises(GenerationShardIntegrityError):
        service.validate(gid)


def test_get_shard_returns_none_for_missing_index(setup):
    service, _, _, gid, _ = setup
    assert service.get_shard(gid, 3) is None
