import uuid
from types import SimpleNamespace
import pytest

from engram.commitment.mmr import (
    MMRState, Peak, calculate_root, verify_membership_proof,
    verify_consistency_proof, append_leaf,
)
from engram.metadata.mmr.services import MMRMetadataService
from engram.metadata.repositories import CorruptMetadataError


class FakeObjects:
    def __init__(self, obj):
        self.obj = obj
    def get(self, object_id):
        return self.obj if self.obj.object_id == object_id else None


class FakeVersions:
    def __init__(self):
        self.versions = {}
    def get(self, object_id, sequence):
        return self.versions.get((object_id, sequence))


class FakeStates:
    def __init__(self):
        self.records = {}
    def get(self, oid):
        return self.records.get(oid)
    def put(self, record, batch):
        batch.stage_state(record)


class FakeNodes:
    def __init__(self):
        self.nodes = {}
    def get(self, oid, start, height):
        return self.nodes.get((oid, start, height))
    def put_all(self, records, batch):
        batch.stage_nodes(records)
    def list_for_object(self, oid):
        return tuple(sorted((r for (o, _, _), r in self.nodes.items() if o == oid),
                           key=lambda r: (r.start_leaf, r.height)))


class FakeBatch:
    def __init__(self, states, nodes):
        self.states, self.nodes = states, nodes
        self.pending_states, self.pending_nodes = [], []
    def stage_state(self, record):
        self.pending_states.append(record)
    def stage_nodes(self, records):
        self.pending_nodes.extend(records)
    def commit(self):
        for record in self.pending_states:
            self.states.records[record.object_id] = record
        for record in self.pending_nodes:
            self.nodes.nodes[(record.object_id, record.start_leaf, record.height)] = record


class FakeCommitments:
    def compute_manifest_hash(self, version):
        return version.manifest_hash



@pytest.fixture
def service_env():
    object_id = str(uuid.uuid4())
    obj = SimpleNamespace(object_id=object_id, head_sequence=0, head_version_id=None)
    objects, versions, states, nodes = FakeObjects(obj), FakeVersions(), FakeStates(), FakeNodes()
    service = MMRMetadataService(object(), commitment_service=FakeCommitments(),
                               object_repository=objects, version_repository=versions,
                               state_repository=states, node_repository=nodes)
    return object_id, obj, versions, states, nodes, service


def test_stage_append_updates_state_and_internal_nodes(service_env):
    oid, obj, versions, states, nodes, service = service_env
    hashes = [bytes([i]) * 32 for i in range(1, 9)]
    for seq, digest in enumerate(hashes, 1):
        versions.versions[(oid, seq)] = SimpleNamespace(object_id=oid, sequence=seq,
                                                      version_id=f"v{seq}", manifest_hash=digest)
        batch = FakeBatch(states, nodes)
        rec = service.stage_append(oid, seq, digest, expected_leaf_count=seq-1, batch=batch)
        batch.commit()
        obj.head_sequence, obj.head_version_id = seq, f"v{seq}"
        assert rec.state.leaf_count == seq
    assert len(nodes.nodes) == 7  # nodes created across appends to 8 leaves
    assert service.validate(oid) is True


def test_membership_proofs_for_all_leaves_verify(service_env):
    oid, obj, versions, states, nodes, service = service_env
    hashes = [bytes([i]) * 32 for i in range(1, 8)]
    state = MMRState(0, ())
    for seq, digest in enumerate(hashes, 1):
        versions.versions[(oid, seq)] = SimpleNamespace(object_id=oid, sequence=seq,
                                                      version_id=f"v{seq}", manifest_hash=digest)
        batch = FakeBatch(states, nodes)
        record = service.stage_append(oid, seq, digest, expected_leaf_count=seq-1, batch=batch)
        batch.commit()
        obj.head_sequence, obj.head_version_id = seq, f"v{seq}"
        state = record.state
    root = calculate_root(state)
    for seq in range(1, 8):
        proof = service.get_membership_proof(oid, seq)
        assert verify_membership_proof(proof, root)


def test_consistency_proof_round_trip(service_env):
    oid, obj, versions, states, nodes, service = service_env
    hashes = [bytes([i]) * 32 for i in range(1, 10)]
    for seq, digest in enumerate(hashes, 1):
        versions.versions[(oid, seq)] = SimpleNamespace(object_id=oid, sequence=seq,
                                                      version_id=f"v{seq}", manifest_hash=digest)
        batch = FakeBatch(states, nodes)
        record = service.stage_append(oid, seq, digest, expected_leaf_count=seq-1, batch=batch)
        batch.commit()
        obj.head_sequence, obj.head_version_id = seq, f"v{seq}"
    # Reconstruct the authentic prefix state from test hashes.
    old_state = MMRState(0, ())
    for digest in hashes[:5]:
        old_state = append_leaf(old_state, digest)
    proof = service.get_consistency_proof(oid, 5, 9)
    assert verify_consistency_proof(old_state, proof,
                                  expected_old_root=calculate_root(old_state),
                                  expected_new_root=service.get_root(oid))


def test_stage_append_rejects_head_conflict(service_env):
    oid, obj, versions, states, nodes, service = service_env
    with pytest.raises(CorruptMetadataError, match="Head"):
        service.stage_append(oid, 1, b"a"*32, expected_leaf_count=1, batch=FakeBatch(states, nodes))


def test_validation_detects_missing_state(service_env):
    oid, obj, versions, states, nodes, service = service_env
    obj.head_sequence = 1
    obj.head_version_id = "v1"
    versions.versions[(oid, 1)] = SimpleNamespace(object_id=oid, sequence=1,
                                                   version_id="v1", manifest_hash=b"a"*32)
    with pytest.raises(CorruptMetadataError, match="missing"):
        service.validate(oid)
