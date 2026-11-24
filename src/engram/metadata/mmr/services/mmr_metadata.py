"""Metadata integration for the Version History MMR."""

from __future__ import annotations

from engram.commitment.mmr import (
    MMRState,
    Peak,
    append_leaf_with_nodes,
    calculate_root,
    hash_leaf,
    create_membership_proof,
    membership_sibling_positions,
    canonical_subtree_heights,
    create_consistency_proof,
)
from engram.commitment.version import VersionCommitmentService
from engram.metadata.repositories import CorruptMetadataError
from ..models import MMRNodeRecord, MMRStateRecord, MMR_PROTOCOL


class MMRMetadataService:
    """Resolve MMR proof hashes from Version records and internal-node metadata."""

    def __init__(
        self,
        db,
        *,
        commitment_service=None,
        object_repository=None,
        version_repository=None,
        state_repository=None,
        node_repository=None,
    ):
        if db is None:
            raise TypeError("db is required")
        self._db = db
        # Import storage adapters lazily so injected repositories can be used
        # without a local RocksDB dependency in unit tests.
        if object_repository is None or version_repository is None:
            from engram.metadata.logical.repositories import (
                ObjectRepository,
                VersionRepository,
            )
        if state_repository is None or node_repository is None:
            from ..repositories import MMRNodeRepository, MMRStateRepository
        self._objects = object_repository or ObjectRepository(db)
        self._versions = version_repository or VersionRepository(db)
        self._states = state_repository or MMRStateRepository(db)
        self._nodes = node_repository or MMRNodeRepository(db)
        self._commitments = commitment_service or VersionCommitmentService()

    @staticmethod
    def _empty_state():
        return MMRState(leaf_count=0, peaks=())

    def get_state(self, object_id: str) -> MMRState:
        obj = self._objects.get(object_id)
        if obj is None:
            raise ValueError(f"Object {object_id!r} does not exist")
        record = self._states.get(object_id)
        if record is None:
            if obj.head_sequence != 0:
                raise CorruptMetadataError(
                    "Object Head has Versions but MMR state is missing"
                )
            return self._empty_state()
        if record.state.leaf_count != obj.head_sequence:
            raise CorruptMetadataError("MMR leaf_count does not match Object Head")
        return record.state

    def get_root(self, object_id: str) -> bytes:
        return calculate_root(self.get_state(object_id))

    def stage_append(
        self,
        object_id: str,
        sequence: int,
        version_manifest_hash: bytes,
        *,
        expected_leaf_count: int,
        batch,
    ) -> MMRStateRecord:
        """Stage new internal nodes and state in the caller-owned batch."""
        if batch is None:
            raise TypeError("batch is required")
        obj = self._objects.get(object_id)
        if obj is None:
            raise ValueError(f"Object {object_id!r} does not exist")
        if (
            isinstance(expected_leaf_count, bool)
            or not isinstance(expected_leaf_count, int)
            or expected_leaf_count < 0
        ):
            raise ValueError("expected_leaf_count must be a non-negative integer")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise ValueError("sequence must be a positive integer")
        if obj.head_sequence != expected_leaf_count:
            raise CorruptMetadataError("Object Head does not match expected_leaf_count")
        if sequence != expected_leaf_count + 1:
            raise ValueError("sequence must be expected_leaf_count + 1")

        stored = self._states.get(object_id)
        if stored is None:
            if expected_leaf_count != 0:
                raise CorruptMetadataError("MMR state is missing for non-empty Object")
            state = self._empty_state()
        else:
            state = stored.state
            if state.leaf_count != expected_leaf_count:
                raise CorruptMetadataError(
                    "Stored MMR state count does not match expected count"
                )

        result = append_leaf_with_nodes(state, version_manifest_hash)
        nodes = tuple(
            MMRNodeRecord(object_id, n.start_leaf, n.height, n.hash)
            for n in result.created_nodes
        )
        self._nodes.put_all(nodes, batch)
        record = MMRStateRecord(object_id, MMR_PROTOCOL, result.state)
        self._states.put(record, batch)
        return record

    def _require_version(self, object_id: str, sequence: int):
        version = self._versions.get(object_id, sequence)
        if version is None:
            raise CorruptMetadataError(f"Missing Version {object_id!r}/{sequence}")
        if version.object_id != object_id or version.sequence != sequence:
            raise CorruptMetadataError(
                "Version identity does not match requested coordinates"
            )
        return version

    def _manifest_hash(self, object_id: str, sequence: int) -> bytes:
        return self._commitments.compute_manifest_hash(
            self._require_version(object_id, sequence)
        )

    def _resolve_subtree_hash(
        self, object_id: str, start_leaf: int, height: int
    ) -> bytes:
        if height == 0:
            return hash_leaf(self._manifest_hash(object_id, start_leaf + 1))
        record = self._nodes.get(object_id, start_leaf, height)
        if record is None:
            raise CorruptMetadataError(
                f"Missing MMR internal node ({start_leaf}, {height}) for Object {object_id!r}"
            )
        return record.hash

    def get_membership_proof(self, object_id: str, sequence: int):
        state = self.get_state(object_id)
        if (
            isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or not 1 <= sequence <= state.leaf_count
        ):
            raise ValueError("sequence must be within the current Version history")
        index = sequence - 1
        version_hash = self._manifest_hash(object_id, sequence)
        positions = membership_sibling_positions(index, state.leaf_count)
        siblings = tuple(
            self._resolve_subtree_hash(object_id, p.start_leaf, p.height)
            for p in positions
        )
        return create_membership_proof(
            state,
            leaf_index=index,
            version_manifest_hash=version_hash,
            siblings=siblings,
        )

    def get_consistency_proof(
        self, object_id: str, old_leaf_count: int, new_leaf_count: int | None = None
    ):
        state = self.get_state(object_id)
        if new_leaf_count is None:
            new_leaf_count = state.leaf_count
        if (
            isinstance(old_leaf_count, bool)
            or not isinstance(old_leaf_count, int)
            or isinstance(new_leaf_count, bool)
            or not isinstance(new_leaf_count, int)
            or old_leaf_count < 0
            or new_leaf_count < old_leaf_count
            or new_leaf_count > state.leaf_count
        ):
            raise ValueError(
                "consistency range must satisfy 0 <= old <= new <= current leaf_count"
            )
        heights = canonical_subtree_heights(old_leaf_count, new_leaf_count)
        cursor, appended = old_leaf_count, []
        for height in heights:
            appended.append(
                Peak(height, self._resolve_subtree_hash(object_id, cursor, height))
            )
            cursor += 1 << height
        return create_consistency_proof(old_leaf_count, new_leaf_count, tuple(appended))

    def validate(self, object_id: str) -> bool:
        """Rebuild state and all persisted internal nodes from Version history."""
        obj = self._objects.get(object_id)
        if obj is None:
            raise ValueError(f"Object {object_id!r} does not exist")
        stored = self._states.get(object_id)
        persisted_nodes = self._nodes.list_for_object(object_id)
        if obj.head_sequence == 0:
            if stored is not None or persisted_nodes:
                raise CorruptMetadataError(
                    "Empty Object must not have persisted MMR metadata"
                )
            return True
        if stored is None:
            raise CorruptMetadataError("MMR state is missing for non-empty Object")
        if stored.state.leaf_count != obj.head_sequence:
            raise CorruptMetadataError("MMR state count does not match Object Head")
        latest = self._require_version(object_id, obj.head_sequence)
        if latest.version_id != obj.head_version_id:
            raise CorruptMetadataError(
                "Object Head does not reference the latest Version"
            )

        rebuilt, expected_nodes = self._empty_state(), {}
        for seq in range(1, obj.head_sequence + 1):
            append = append_leaf_with_nodes(
                rebuilt, self._manifest_hash(object_id, seq)
            )
            rebuilt = append.state
            for node in append.created_nodes:
                expected_nodes[(node.start_leaf, node.height)] = node.hash
        if rebuilt != stored.state:
            raise CorruptMetadataError(
                "Persisted MMR state differs from rebuilt Version history"
            )
        actual_nodes = {(n.start_leaf, n.height): n.hash for n in persisted_nodes}
        if actual_nodes != expected_nodes:
            raise CorruptMetadataError(
                "Persisted MMR internal nodes differ from rebuilt history"
            )
        return True
