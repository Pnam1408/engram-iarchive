from uuid import uuid4
from types import SimpleNamespace

import pytest

from engram.metadata.physical.models import (
    GenerationShardCommitment,
    GenerationShardCommitmentEntry,
    GenerationStatus,
    SHARD_COMMITMENT_PROTOCOL,
    BN254_SCALAR_MODULUS,
)


def field_bytes(value: int) -> bytes:
    return value.to_bytes(32, "big")


def test_commitment_model_accepts_valid_root():
    model = GenerationShardCommitment(
        str(uuid4()), SHARD_COMMITMENT_PROTOCOL, 4, field_bytes(123), 100
    )
    assert model.global_root == field_bytes(123)



@pytest.mark.parametrize("root", [b"\x00" * 31, b"\x00" * 33, field_bytes(BN254_SCALAR_MODULUS)])
def test_commitment_model_rejects_invalid_field_root(root):
    with pytest.raises((TypeError, ValueError)):
        GenerationShardCommitment(str(uuid4()), SHARD_COMMITMENT_PROTOCOL, 1, root, 0)


def test_entry_model_validation():
    entry = GenerationShardCommitmentEntry(str(uuid4()), 0, field_bytes(1))
    assert entry.shard_index == 0
    with pytest.raises(ValueError):
        GenerationShardCommitmentEntry(str(uuid4()), -1, field_bytes(1))
    with pytest.raises(ValueError):
        GenerationShardCommitmentEntry(str(uuid4()), 0, field_bytes(BN254_SCALAR_MODULUS))

class _FakeCommitment:
    def __init__(self, roots=(5, 9), global_root=15):
        self.shard_roots = roots
        self.shard_count = len(roots)
        self.global_root = global_root



class _GenerationRepo:
    def __init__(self, status=GenerationStatus.ENCODED, total_shards=2):
        self.status = status
        self.total_shards = total_shards

    def get(self, generation_id):
        from engram.metadata.physical.models import Generation
        encoded_at = 12 if self.status not in (GenerationStatus.OPEN, GenerationStatus.SEALED, GenerationStatus.ENCODING) else None
        return Generation(generation_id, 1, self.total_shards, self.status, 10, 20, sealed_at=11, encoded_at=encoded_at)



class _ShardRepo:
    def __init__(self, count=2):
        self.count = count

    def list_for_generation(self, generation_id):
        return tuple(SimpleNamespace(generation_id=generation_id, shard_index=i) for i in range(self.count))



class _CommitmentRepo:
    def __init__(self):
        self.records = {}

    def get(self, generation_id):
        return self.records.get(generation_id)



class _EntryRepo:
    def __init__(self):
        self.records = {}

    def list_for_generation(self, generation_id):
        return tuple(self.records.get(generation_id, ()))

    def get(self, generation_id, shard_index):
        return next((e for e in self.records.get(generation_id, ()) if e.shard_index == shard_index), None)



@pytest.fixture
def metadata_service_fakes():
    class CommitmentRepo(_CommitmentRepo):
        def put(self, record, batch):
            self.records[record.generation_id] = record

    class EntryRepo(_EntryRepo):
        def put_all(self, entries, batch):
            by_generation = {}
            for entry in entries:
                by_generation.setdefault(entry.generation_id, []).append(entry)
            self.records.update({key: tuple(value) for key, value in by_generation.items()})

    return CommitmentRepo(), EntryRepo()



def test_metadata_service_registers_and_validates_bundle(monkeypatch, metadata_service_fakes):
    import engram.commitment.shard_merkle.service as commitment_module
    from engram.metadata.physical.services.shard_commitment_metadata import ShardCommitmentMetadataService
    monkeypatch.setattr(commitment_module, "ShardCommitment", _FakeCommitment)
    commitment_repo, entry_repo = metadata_service_fakes
    generation_id = str(uuid4())
    service = ShardCommitmentMetadataService(_GenerationRepo(), _ShardRepo(), commitment_repo, entry_repo)
    class Batch:
        pass
    bundle = service.register(generation_id, _FakeCommitment(), batch=Batch(), created_at=50)
    assert bundle.commitment.global_root == field_bytes(15)
    assert tuple(e.shard_index for e in bundle.entries) == (0, 1)
    assert service.get_bundle(generation_id) == bundle
    assert service.get_shard_root(generation_id, 1).shard_root == field_bytes(9)
    assert service.validate(generation_id) is True


def test_metadata_service_rejects_incomplete_shard_catalog(monkeypatch, metadata_service_fakes):
    import engram.commitment.shard_merkle.service as commitment_module
    from engram.metadata.physical.services.shard_commitment_metadata import ShardCommitmentMetadataService, ShardCommitmentPreconditionError
    monkeypatch.setattr(commitment_module, "ShardCommitment", _FakeCommitment)
    commitment_repo, entry_repo = metadata_service_fakes
    service = ShardCommitmentMetadataService(_GenerationRepo(), _ShardRepo(count=1), commitment_repo, entry_repo)
    class Batch:
        pass
    with pytest.raises(ShardCommitmentPreconditionError):
        service.register(str(uuid4()), _FakeCommitment(), batch=Batch(), created_at=50)


def test_metadata_service_rejects_non_encoded_generation(monkeypatch, metadata_service_fakes):
    import engram.commitment.shard_merkle.service as commitment_module
    from engram.metadata.physical.services.shard_commitment_metadata import ShardCommitmentMetadataService, ShardCommitmentPreconditionError
    monkeypatch.setattr(commitment_module, "ShardCommitment", _FakeCommitment)
    commitment_repo, entry_repo = metadata_service_fakes
    service = ShardCommitmentMetadataService(_GenerationRepo(status=GenerationStatus.ENCODING), _ShardRepo(), commitment_repo, entry_repo)
    class Batch:
        pass
    with pytest.raises(ShardCommitmentPreconditionError):
        service.register(str(uuid4()), _FakeCommitment(), batch=Batch(), created_at=50)


def test_metadata_service_rejects_duplicate_registration(monkeypatch, metadata_service_fakes):
    import engram.commitment.shard_merkle.service as commitment_module
    from engram.metadata.physical.services.shard_commitment_metadata import ShardCommitmentMetadataService, ShardCommitmentAlreadyRegisteredError
    monkeypatch.setattr(commitment_module, "ShardCommitment", _FakeCommitment)
    commitment_repo, entry_repo = metadata_service_fakes
    generation_id = str(uuid4())
    service = ShardCommitmentMetadataService(_GenerationRepo(), _ShardRepo(), commitment_repo, entry_repo)
    class Batch:
        pass
    service.register(generation_id, _FakeCommitment(), batch=Batch(), created_at=50)
    with pytest.raises(ShardCommitmentAlreadyRegisteredError):
        service.register(generation_id, _FakeCommitment(), batch=Batch(), created_at=51)



class _MemoryDB:
    def __init__(self):
        self.values = {}
    def get(self, cf, key):
        return self.values.get((cf, key))
    def iter_prefix(self, cf, prefix):
        return iter(sorted((key, value) for (family, key), value in self.values.items() if family == cf and key.startswith(prefix)))


class _MemoryBatch:
    def __init__(self, db):
        self.db = db
        self.pending = []
    def put(self, cf, key, value):
        self.pending.append((cf, key, value))
    def commit(self):
        for cf, key, value in self.pending:
            self.db.values[(cf, key)] = value


def test_commitment_repositories_round_trip_records():
    from engram.metadata.physical.repositories import GenerationShardCommitmentRepository, GenerationShardCommitmentEntryRepository
    from engram.metadata.database import ColumnFamily
    db = _MemoryDB()
    batch = _MemoryBatch(db)
    record_repo = GenerationShardCommitmentRepository(db)
    entry_repo = GenerationShardCommitmentEntryRepository(db)
    generation_id = str(uuid4())
    record = GenerationShardCommitment(generation_id, SHARD_COMMITMENT_PROTOCOL, 2, field_bytes(15), 50)
    entries = tuple(GenerationShardCommitmentEntry(generation_id, i, field_bytes(root)) for i, root in enumerate((5, 9)))
    record_repo.put(record, batch)
    entry_repo.put_all(entries, batch)
    batch.commit()
    assert record_repo.get(generation_id) == record
    assert entry_repo.list_for_generation(generation_id) == entries
    assert entry_repo.get(generation_id, 1) == entries[1]
    assert len(db.values) == 3
