import uuid
from dataclasses import replace

import pytest

from engram.metadata.physical.models import (
    Generation, GenerationManifest, GenerationManifestEntry,
    GenerationShard, GenerationStatus,
)
from engram.metadata.physical.services import (
    GenerationAlreadyExistsError, GenerationConcurrencyError,
    GenerationLifecycleService, GenerationNotFoundError,
    GenerationPreconditionError, InvalidGenerationTransitionError,
    ShardAvailabilitySnapshot,
)


class Batch:
    pass


class Generations:
    def __init__(self):
        self.items = {}

    def get(self, key):
        return self.items.get(key)

    def put(self, value, batch):
        self.items[value.generation_id] = value


class Manifests:
    def __init__(self):
        self.items = {}

    def get(self, key):
        return self.items.get(key)


class Entries:
    def __init__(self):
        self.items = {}

    def list_for_generation(self, key):
        return tuple(self.items.get(key, ()))


class Shards:
    def __init__(self):
        self.items = {}

    def list_for_generation(self, key):
        return tuple(self.items.get(key, ()))


@pytest.fixture
def setup():
    repos = (Generations(), Manifests(), Entries(), Shards())
    tick = iter(range(100, 200))
    service = GenerationLifecycleService(*repos, clock=lambda: next(tick))
    batch = Batch()
    gid = str(uuid.uuid4())
    generation = Generation(gid, 2, 3, GenerationStatus.OPEN, 1, 1)
    return service, repos, batch, generation


def test_create_and_query(setup):
    service, repos, batch, generation = setup
    service.create(generation, batch=batch)
    assert service.get(generation.generation_id) == generation
    assert service.require(generation.generation_id) == generation
    assert service.get_status(generation.generation_id) is GenerationStatus.OPEN
    with pytest.raises(GenerationAlreadyExistsError):
        service.create(generation, batch=batch)
    with pytest.raises(GenerationNotFoundError):
        service.require(str(uuid.uuid4()))


def test_create_requires_open(setup):
    service, _, batch, generation = setup
    with pytest.raises(GenerationPreconditionError):
        service.create(replace(generation, status=GenerationStatus.FAILED,
                               completed_at=2, updated_at=2), batch=batch)


def test_full_lifecycle_and_availability_recovery(setup):
    service, repos, batch, generation = setup
    generations, manifests, entries, shards = repos
    service.create(generation, batch=batch)

    entry = GenerationManifestEntry(
        generation.generation_id, str(uuid.uuid4()), "object-1",
        str(uuid.uuid4()), 0, 10, b"x" * 32,
    )
    manifest = GenerationManifest(generation.generation_id, 1, 10, b"m" * 32, 2)
    manifests.items[generation.generation_id] = manifest
    entries.items[generation.generation_id] = (entry,)

    current = service.seal(generation.generation_id, 1, batch=batch)
    assert current.status is GenerationStatus.SEALED
    assert current.sealed_at == current.updated_at

    current = service.start_encoding(generation.generation_id, current.updated_at, batch=batch)
    assert current.status is GenerationStatus.ENCODING

    shards.items[generation.generation_id] = tuple(
        GenerationShard(generation.generation_id, i, 10, bytes([i]) * 32)
        for i in range(3)
    )
    current = service.mark_encoded(generation.generation_id, current.updated_at, batch=batch)
    assert current.status is GenerationStatus.ENCODED
    assert current.encoded_at == current.updated_at

    current = service.start_storing(generation.generation_id, current.updated_at, batch=batch)
    assert current.status is GenerationStatus.STORING

    snapshot = ShardAvailabilitySnapshot(generation.generation_id, frozenset({0, 1}))
    current = service.mark_degraded(generation.generation_id, current.updated_at, snapshot, batch=batch)
    completed = current.completed_at
    assert current.status is GenerationStatus.DEGRADED

    full = ShardAvailabilitySnapshot(generation.generation_id, frozenset({0, 1, 2}))
    current = service.mark_available(generation.generation_id, current.updated_at, full, batch=batch)
    assert current.status is GenerationStatus.AVAILABLE
    assert current.completed_at == completed

    current = service.mark_degraded(generation.generation_id, current.updated_at, snapshot, batch=batch)
    assert current.completed_at == completed


def test_availability_thresholds_and_reject_wrong_classification(setup):
    service, repos, batch, generation = setup
    generations, _, _, shards = repos
    # Arrange a valid STORING generation with complete shard metadata.
    current = replace(generation, status=GenerationStatus.STORING,
                      sealed_at=2, encoded_at=3, updated_at=3)
    generations.items[generation.generation_id] = current
    shards.items[generation.generation_id] = tuple(
        GenerationShard(generation.generation_id, i, 10, b"s" * 32)
        for i in range(3)
    )
    with pytest.raises(GenerationPreconditionError):
        service.mark_available(
            generation.generation_id, 3,
            ShardAvailabilitySnapshot(generation.generation_id, frozenset({0, 1})),
            batch=batch,
        )
    lost = service.mark_lost(
        generation.generation_id, 3,
        ShardAvailabilitySnapshot(generation.generation_id, frozenset({0})),
        batch=batch,
    )
    assert lost.status is GenerationStatus.LOST
    assert lost.completed_at is not None
    with pytest.raises(InvalidGenerationTransitionError):
        service.mark_available(
            generation.generation_id, lost.updated_at,
            ShardAvailabilitySnapshot(generation.generation_id, frozenset({0, 1, 2})),
            batch=batch,
        )


def test_stale_timestamp_rejected(setup):
    service, repos, batch, generation = setup
    repos[0].items[generation.generation_id] = generation
    with pytest.raises(GenerationConcurrencyError):
        service.mark_failed(generation.generation_id, 999, batch=batch)


def test_failed_is_terminal_and_sets_completion(setup):
    service, repos, batch, generation = setup
    repos[0].items[generation.generation_id] = generation
    failed = service.mark_failed(generation.generation_id, 1, batch=batch)
    assert failed.status is GenerationStatus.FAILED
    assert failed.completed_at is not None
    with pytest.raises(InvalidGenerationTransitionError):
        service.mark_failed(generation.generation_id, failed.updated_at, batch=batch)


def test_manifest_precondition_and_no_write_on_failure(setup):
    service, repos, batch, generation = setup
    repos[0].items[generation.generation_id] = generation
    with pytest.raises(GenerationPreconditionError):
        service.seal(generation.generation_id, 1, batch=batch)
    assert repos[0].items[generation.generation_id] is generation


def test_snapshot_validation(setup):
    service, repos, batch, generation = setup
    current = replace(generation, status=GenerationStatus.STORING,
                      sealed_at=2, encoded_at=3, updated_at=3)
    repos[0].items[generation.generation_id] = current
    repos[3].items[generation.generation_id] = tuple(
        GenerationShard(generation.generation_id, i, 10, b"s" * 32)
        for i in range(3)
    )
    bad = ShardAvailabilitySnapshot(generation.generation_id, frozenset({0, 3}))
    with pytest.raises(GenerationPreconditionError):
        service.mark_lost(generation.generation_id, 3, bad, batch=batch)
