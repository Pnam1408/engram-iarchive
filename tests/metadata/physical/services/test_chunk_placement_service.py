import uuid

import pytest

from engram.metadata.physical.models import (
    ChunkPlacement,
    Generation,
    GenerationManifestEntry,
    GenerationStatus,
)
from engram.metadata.physical.services import (
    ChunkPlacementAlreadyRegisteredError,
    ChunkPlacementGenerationNotFoundError,
    ChunkPlacementIntegrityError,
    ChunkPlacementNotFoundError,
    ChunkPlacementPreconditionError,
    ChunkPlacementService,
)


class Batch:
    pass


class Generations:
    def __init__(self):
        self.items = {}

    def get(self, key):
        return self.items.get(key)


class Entries:
    def __init__(self):
        self.items = {}

    def list_for_generation(self, key):
        return tuple(self.items.get(key, ()))


class Placements:
    def __init__(self):
        self.items = {}
        self.staged = []

    def get(self, key):
        return self.items.get(key)

    def list_for_chunk(self, object_id, chunk_id):
        return tuple(
            item
            for item in self.items.values()
            if item.object_id == object_id and item.chunk_id == chunk_id
        )

    def put(self, placement, batch):
        self.staged.append((placement, batch))


@pytest.fixture
def setup():
    generations, entries, placements = Generations(), Entries(), Placements()
    service = ChunkPlacementService(generations, entries, placements)
    gid = str(uuid.uuid4())
    chunk_id = str(uuid.uuid4())
    record_id = str(uuid.uuid4())
    entry = GenerationManifestEntry(
        gid, record_id, "object-a", chunk_id, 0, 8, b"r" * 32
    )
    placement = ChunkPlacement(
        str(uuid.uuid4()), "object-a", chunk_id, gid, record_id, 100
    )
    generations.items[gid] = Generation(gid, 2, 4, GenerationStatus.OPEN, 10, 10)
    entries.items[gid] = (entry,)
    return service, generations, entries, placements, gid, entry, placement


def test_register_stages_placement_when_it_matches_persisted_entry(setup):
    service, _, _, repository, _, entry, placement = setup
    batch = Batch()

    result = service.register(placement, entry, batch=batch)

    assert result == placement
    assert repository.staged == [(placement, batch)]


def test_register_requires_existing_generation(setup):
    service, _, _, repository, _, entry, placement = setup
    missing = ChunkPlacement(
        placement.placement_id,
        placement.object_id,
        placement.chunk_id,
        str(uuid.uuid4()),
        placement.record_id,
        placement.created_at,
    )
    with pytest.raises(ChunkPlacementGenerationNotFoundError):
        service.register(missing, entry, batch=Batch())
    assert not repository.staged


def test_register_only_while_generation_open(setup):
    service, generations, _, repository, gid, entry, placement = setup
    generations.items[gid] = Generation(
        gid, 2, 4, GenerationStatus.SEALED, 10, 11, sealed_at=11
    )
    with pytest.raises(ChunkPlacementPreconditionError):
        service.register(placement, entry, batch=Batch())
    assert not repository.staged


def test_register_rejects_placement_that_does_not_match_entry(setup):
    service, _, _, repository, _, entry, placement = setup
    wrong = ChunkPlacement(
        placement.placement_id,
        placement.object_id,
        placement.chunk_id,
        placement.generation_id,
        str(uuid.uuid4()),
        placement.created_at,
    )
    with pytest.raises(ChunkPlacementPreconditionError):
        service.register(wrong, entry, batch=Batch())
    assert not repository.staged


def test_register_accepts_entry_staged_in_same_caller_batch(setup):
    service, _, entries, repository, gid, entry, placement = setup
    entries.items[gid] = ()  # Not visible yet: it is expected to be in caller batch.
    batch = Batch()

    service.register(placement, entry, batch=batch)

    assert repository.staged == [(placement, batch)]


def test_register_rejects_duplicate_chunk_placement_in_same_generation(setup):
    service, _, _, repository, _, entry, placement = setup
    other = ChunkPlacement(
        str(uuid.uuid4()),
        placement.object_id,
        placement.chunk_id,
        placement.generation_id,
        placement.record_id,
        101,
    )
    repository.items[other.placement_id] = other
    with pytest.raises(ChunkPlacementAlreadyRegisteredError):
        service.register(placement, entry, batch=Batch())
    assert not repository.staged


def test_same_chunk_may_have_placement_in_another_generation(setup):
    service, generations, _, repository, _, entry, placement = setup
    other_generation_id = str(uuid.uuid4())
    generations.items[other_generation_id] = Generation(
        other_generation_id, 2, 4, GenerationStatus.OPEN, 10, 10
    )
    other = ChunkPlacement(
        str(uuid.uuid4()),
        placement.object_id,
        placement.chunk_id,
        other_generation_id,
        str(uuid.uuid4()),
        99,
    )
    repository.items[other.placement_id] = other

    batch = Batch()
    service.register(placement, entry, batch=batch)

    assert repository.staged == [(placement, batch)]


def test_get_and_list_for_chunk(setup):
    service, _, _, repository, _, _, placement = setup
    repository.items[placement.placement_id] = placement

    assert service.get(placement.placement_id) == placement
    assert service.list_for_chunk(placement.object_id, placement.chunk_id) == (
        placement,
    )
    assert service.get(str(uuid.uuid4())) is None


def test_validate_one_placement(setup):
    service, _, _, repository, _, _, placement = setup
    repository.items[placement.placement_id] = placement

    assert service.validate(placement.placement_id) is True


def test_validate_rejects_missing_or_mismatched_manifest_entry(setup):
    service, _, entries, repository, gid, entry, placement = setup
    repository.items[placement.placement_id] = placement
    entries.items[gid] = ()

    with pytest.raises(ChunkPlacementIntegrityError):
        service.validate(placement.placement_id)
    with pytest.raises(ChunkPlacementNotFoundError):
        service.validate(str(uuid.uuid4()))


def test_validate_generation_requires_one_matching_placement_per_entry(setup):
    service, _, _, repository, _, _, placement = setup
    repository.items[placement.placement_id] = placement

    assert service.validate_generation(placement.generation_id) is True

    repository.items.clear()
    with pytest.raises(ChunkPlacementIntegrityError):
        service.validate_generation(placement.generation_id)


def test_validate_generation_detects_mismatched_record(setup):
    service, _, _, repository, _, _, placement = setup
    wrong = ChunkPlacement(
        placement.placement_id,
        placement.object_id,
        placement.chunk_id,
        placement.generation_id,
        str(uuid.uuid4()),
        placement.created_at,
    )
    repository.items[wrong.placement_id] = wrong

    with pytest.raises(ChunkPlacementIntegrityError):
        service.validate_generation(placement.generation_id)
