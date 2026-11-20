import uuid

import pytest

from engram.metadata.physical.models import (
    Generation,
    GenerationManifest,
    GenerationManifestEntry,
    GenerationStatus,
)
from engram.metadata.physical.services import (
    GenerationManifestAlreadyRegisteredError,
    GenerationManifestBundle,
    GenerationManifestGenerationNotFoundError,
    GenerationManifestIntegrityError,
    GenerationManifestNotFoundError,
    GenerationManifestPreconditionError,
    GenerationManifestService,
)


class Batch:
    pass


class Generations:
    def __init__(self):
        self.items = {}

    def get(self, key):
        return self.items.get(key)


class Manifests:
    def __init__(self):
        self.items = {}
        self.staged = []

    def get(self, key):
        return self.items.get(key)

    def put(self, value, batch):
        self.staged.append((value, batch))


class Entries:
    def __init__(self):
        self.items = {}
        self.staged = []

    def list_for_generation(self, key):
        return tuple(self.items.get(key, ()))

    def put_all(self, manifest, entries, batch):
        manifest.validate_entries(entries)
        self.staged.append((manifest, entries, batch))


@pytest.fixture
def setup():
    generations, manifests, entries = Generations(), Manifests(), Entries()
    service = GenerationManifestService(generations, manifests, entries)
    gid = str(uuid.uuid4())
    generations.items[gid] = Generation(gid, 2, 3, GenerationStatus.OPEN, 10, 10)
    record_entries = (
        GenerationManifestEntry(
            gid,
            str(uuid.uuid4()),
            "object-a",
            str(uuid.uuid4()),
            0,
            5,
            b"a" * 32,
        ),
        GenerationManifestEntry(
            gid,
            str(uuid.uuid4()),
            "object-b",
            str(uuid.uuid4()),
            5,
            7,
            b"b" * 32,
        ),
    )
    manifest = GenerationManifest(gid, 2, 12, b"m" * 32, 11)
    return service, generations, manifests, entries, manifest, record_entries


def test_register_stages_manifest_and_entries_in_caller_batch(setup):
    service, _, manifests, entries, manifest, records = setup
    batch = Batch()
    result = service.register(manifest, records, batch)

    assert result == GenerationManifestBundle(manifest, records)
    assert manifests.staged == [(manifest, batch)]
    assert entries.staged == [(manifest, records, batch)]


def test_register_requires_existing_generation(setup):
    service, _, _, _, manifest, records = setup
    with pytest.raises(GenerationManifestGenerationNotFoundError):
        service.register(
            GenerationManifest(str(uuid.uuid4()), 2, 12, b"m" * 32, 11),
            records,
            Batch(),
        )


def test_register_only_while_open(setup):
    service, generations, manifests, entries, manifest, records = setup
    generations.items[manifest.generation_id] = Generation(
        manifest.generation_id,
        2,
        3,
        GenerationStatus.SEALED,
        10,
        12,
        sealed_at=12,
    )
    with pytest.raises(GenerationManifestPreconditionError):
        service.register(manifest, records, Batch())
    assert not manifests.staged
    assert not entries.staged


def test_register_rejects_invalid_entries_before_staging(setup):
    service, _, manifests, entries, manifest, records = setup
    invalid = (records[1], records[0])
    with pytest.raises(GenerationManifestPreconditionError):
        service.register(manifest, invalid, Batch())
    assert not manifests.staged
    assert not entries.staged


def test_register_rejects_existing_manifest_or_orphan_entries(setup):
    service, _, manifests, entries, manifest, records = setup
    manifests.items[manifest.generation_id] = manifest
    with pytest.raises(GenerationManifestAlreadyRegisteredError):
        service.register(manifest, records, Batch())

    manifests.items.clear()
    entries.items[manifest.generation_id] = records
    with pytest.raises(GenerationManifestAlreadyRegisteredError):
        service.register(manifest, records, Batch())


def test_getters_and_validate_existing_bundle(setup):
    service, _, manifests, entries, manifest, records = setup
    manifests.items[manifest.generation_id] = manifest
    entries.items[manifest.generation_id] = records

    assert service.get_manifest(manifest.generation_id) == manifest
    assert service.list_entries(manifest.generation_id) == records
    assert service.get_bundle(manifest.generation_id) == GenerationManifestBundle(
        manifest, records
    )
    assert service.validate(manifest.generation_id) is True


def test_missing_manifest_and_orphan_entries_are_distinguished(setup):
    service, _, _, entries, manifest, records = setup
    assert service.get_bundle(manifest.generation_id) is None
    with pytest.raises(GenerationManifestNotFoundError):
        service.validate(manifest.generation_id)

    entries.items[manifest.generation_id] = records
    with pytest.raises(GenerationManifestIntegrityError):
        service.get_bundle(manifest.generation_id)


def test_get_bundle_rejects_corrupt_stored_entry_set(setup):
    service, _, manifests, entries, manifest, records = setup
    manifests.items[manifest.generation_id] = manifest
    entries.items[manifest.generation_id] = (records[0],)
    with pytest.raises(GenerationManifestIntegrityError):
        service.get_bundle(manifest.generation_id)
