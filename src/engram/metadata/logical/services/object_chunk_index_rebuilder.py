from __future__ import annotations

from dataclasses import dataclass
from threading import Lock

from ..models import ChunkRef, CheckpointEntry, Checkpoint, Version
from ..repositories import (
    CheckpointRepository,
    ChunkRepository,
    ObjectChunkIndexRepository,
    ObjectRepository,
    VersionDeltaRepository,
    VersionRepository,
    CheckpointEntryRepository,
    ObjectChunkIndexSequenceConflictError,
)
from ...database import MetadataDB
from ...repositories import CorruptMetadataError
from .canonicalizer import Canonicalizer
from .checkpoint import CheckpointError, CheckpointService
from .delta_applier import DeltaApplier, DeltaApplyError
from .version_signer import verify_ed25519_signature


class ObjectChunkIndexRebuildError(Exception):
    """Base error raised while reconstructing the Object Chunk Index."""


class ObjectChunkIndexTargetError(ObjectChunkIndexRebuildError):
    """The requested target sequence is not a valid Object history position."""


class ObjectChunkIndexHistoryError(ObjectChunkIndexRebuildError):
    """Version or Delta history is missing, inconsistent, or invalid."""


class ObjectChunkIndexCheckpointError(ObjectChunkIndexRebuildError):
    """No usable reconstruction path could be established from Checkpoints."""


class ObjectChunkIndexRebuildConflictError(ObjectChunkIndexRebuildError):
    """The Object Head or index changed before the rebuilt state was persisted."""


@dataclass(frozen=True, slots=True)
class ObjectChunkIndexRebuildResult:
    object_id: str
    sequence: int
    version_id: str | None
    refs: tuple[ChunkRef, ...]
    checkpoint_sequence: int
    replayed_versions: int
    persisted: bool


class ObjectChunkIndexRebuilder:
    """
    Reconstructs the Object Chunk Index from a verified Checkpoint and deltas.

    Checkpoints are trusted after their stored snapshot commitment, entry
    structure, Version binding, Version signature, and immediate parent link
    have been checked. Earlier history before the Checkpoint is not replayed.
    """

    def __init__(
        self,
        db: MetadataDB,
        *,
        canonicalizer: Canonicalizer | None = None,
        delta_applier: DeltaApplier | None = None,
        checkpoint_service: CheckpointService | None = None,
    ) -> None:
        if db is None:
            raise TypeError("db is required")
        self._db = db
        self._objects = ObjectRepository(db)
        self._versions = VersionRepository(db)
        self._deltas = VersionDeltaRepository(db)
        self._chunks = ChunkRepository(db)
        self._index = ObjectChunkIndexRepository(db)
        self._checkpoints = CheckpointRepository(db)
        self._checkpoint_entries = CheckpointEntryRepository(db)
        self._canonicalizer = canonicalizer or Canonicalizer()
        self._applier = delta_applier or DeltaApplier()
        self._checkpoint_service = checkpoint_service or CheckpointService(
            db, canonicalizer=self._canonicalizer, verify_chunk_catalog=True
        )
        self._locks_guard = Lock()
        self._locks: dict[str, Lock] = {}

    def _lock_for(self, object_id: str) -> Lock:
        with self._locks_guard:
            lock = self._locks.get(object_id)
            if lock is None:
                lock = Lock()
                self._locks[object_id] = lock
            return lock

    def reconstruct(
        self,
        object_id: str,
        target_sequence: int,
    ) -> ObjectChunkIndexRebuildResult:
        self._validate_target_sequence(target_sequence)
        obj = self._objects.get(object_id)
        if obj is None:
            raise ObjectChunkIndexTargetError(f"Object {object_id!r} does not exist")
        if target_sequence > obj.head_sequence:
            raise ObjectChunkIndexTargetError(
                f"Target sequence {target_sequence} exceeds Object Head {obj.head_sequence}"
            )

        if target_sequence == 0:
            return ObjectChunkIndexRebuildResult(
                object_id=object_id,
                sequence=0,
                version_id=None,
                refs=(),
                checkpoint_sequence=0,
                replayed_versions=0,
                persisted=False,
            )

        checkpoint_state = self._find_checkpoint_state(object_id, target_sequence)
        if checkpoint_state is None:
            state: tuple[ChunkRef, ...] = ()
            start_sequence = 0
            parent_version: Version | None = None
        else:
            checkpoint, entries, checkpoint_version = checkpoint_state
            state = tuple(
                ChunkRef(
                    chunk_ref_id=entry.chunk_ref_id,
                    chunk_id=entry.chunk_id,
                    order_key=entry.order_key,
                )
                for entry in entries
            )
            start_sequence = checkpoint.sequence
            parent_version = checkpoint_version

        self._validate_state_catalog(object_id, state)
        replayed = 0
        for sequence in range(start_sequence + 1, target_sequence + 1):
            version = self._versions.get(object_id, sequence)
            delta = self._deltas.get(object_id, sequence)
            if version is None:
                raise ObjectChunkIndexHistoryError(
                    f"Missing Version at sequence {sequence}"
                )
            if delta is None:
                raise ObjectChunkIndexHistoryError(
                    f"Missing VersionDelta at sequence {sequence}"
                )
            self._validate_version_and_delta(
                object_id=object_id,
                sequence=sequence,
                version=version,
                delta=delta,
                parent=parent_version,
            )
            for added in delta.added_chunks:
                self._require_catalog_chunk(object_id, added.chunk_id)
            try:
                state = self._applier.apply(state, delta)
            except DeltaApplyError as exc:
                raise ObjectChunkIndexHistoryError(
                    f"Cannot apply VersionDelta at sequence {sequence}"
                ) from exc
            parent_version = version
            replayed += 1

        target_version = self._versions.get(object_id, target_sequence)
        if target_version is None:
            raise ObjectChunkIndexHistoryError(
                f"Missing target Version at sequence {target_sequence}"
            )
        if (
            target_sequence == obj.head_sequence
            and target_version.version_id != obj.head_version_id
        ):
            raise ObjectChunkIndexHistoryError(
                "Object Head does not reference the target Version"
            )
        self._validate_state_catalog(object_id, state)
        # Re-read the Head so a result is not returned as a stable reconstruction
        # if a concurrent writer advanced the Object during replay.
        current_obj = self._objects.get(object_id)
        if current_obj is None or current_obj.head_sequence < target_sequence:
            raise ObjectChunkIndexRebuildConflictError(
                "Object Head changed incompatibly during reconstruction"
            )

        return ObjectChunkIndexRebuildResult(
            object_id=object_id,
            sequence=target_sequence,
            version_id=target_version.version_id,
            refs=state,
            checkpoint_sequence=start_sequence,
            replayed_versions=replayed,
            persisted=False,
        )

    def rebuild_current(self, object_id: str) -> ObjectChunkIndexRebuildResult:
        with self._lock_for(object_id):
            obj_before = self._objects.get(object_id)
            if obj_before is None:
                raise ObjectChunkIndexTargetError(
                    f"Object {object_id!r} does not exist"
                )

            index_snapshot_token = self._index.snapshot_token(object_id)
            result = self.reconstruct(object_id, obj_before.head_sequence)

            obj_after = self._objects.get(object_id)
            if obj_after != obj_before:
                raise ObjectChunkIndexRebuildConflictError(
                    "Object Head changed while rebuilding the index"
                )

            try:
                with self._db.batch() as batch:
                    self._index.stage_replacement(
                        object_id=object_id,
                        expected_snapshot_token=index_snapshot_token,
                        new_sequence=obj_before.head_sequence,
                        refs=result.refs,
                        batch=batch,
                    )

                    # Final optimistic Head check immediately before commit.
                    if self._objects.get(object_id) != obj_before:
                        raise ObjectChunkIndexRebuildConflictError(
                            "Object Head changed before index replacement"
                        )

                    batch.commit()

            except ObjectChunkIndexRebuildConflictError:
                raise
            except ObjectChunkIndexSequenceConflictError as exc:
                raise ObjectChunkIndexRebuildConflictError(
                    "Object Chunk Index changed before replacement"
                ) from exc

            return ObjectChunkIndexRebuildResult(
                object_id=result.object_id,
                sequence=result.sequence,
                version_id=result.version_id,
                refs=result.refs,
                checkpoint_sequence=result.checkpoint_sequence,
                replayed_versions=result.replayed_versions,
                persisted=True,
            )

    def _find_checkpoint_state(
        self, object_id: str, target_sequence: int
    ) -> tuple[Checkpoint, tuple[CheckpointEntry, ...], Version] | None:
        try:
            candidates = self._checkpoints.list_for_object(object_id)
        except CorruptMetadataError as exc:
            raise ObjectChunkIndexCheckpointError(
                "Cannot enumerate Checkpoints for this Object"
            ) from exc
        for checkpoint in reversed(candidates):
            if checkpoint.sequence > target_sequence:
                continue
            try:
                entries = self._checkpoint_service.verify(checkpoint)
                version = self._versions.get(object_id, checkpoint.sequence)
                if version is None:
                    continue
                self._validate_version_signature(version)
                self._validate_checkpoint_parent(version)
                return checkpoint, entries, version
            except (
                CheckpointError,
                CorruptMetadataError,
                ObjectChunkIndexHistoryError,
            ):
                continue
        return None

    def _validate_checkpoint_parent(self, version: Version) -> None:
        if version.sequence == 1:
            if (
                version.parent_version_id is not None
                or version.previous_version_hash is not None
            ):
                raise ObjectChunkIndexHistoryError(
                    "Version 1 has an invalid parent link"
                )
            return
        parent = self._versions.get(version.object_id, version.sequence - 1)
        if parent is None or parent.version_id != version.parent_version_id:
            raise ObjectChunkIndexHistoryError(
                "Checkpoint Version parent is missing or inconsistent"
            )
        if self._canonicalizer.hash_version(parent) != version.previous_version_hash:
            raise ObjectChunkIndexHistoryError(
                "Checkpoint Version previous_version_hash mismatch"
            )

    def _validate_version_signature(self, version: Version) -> None:
        payload = self._canonicalizer.encode_version_signing_payload(version)
        if not verify_ed25519_signature(
            version.writer_public_key, payload, version.signature
        ):
            raise ObjectChunkIndexHistoryError(
                f"Invalid Ed25519 signature at Version {version.sequence}"
            )

    def _validate_version_and_delta(
        self,
        *,
        object_id: str,
        sequence: int,
        version: Version,
        delta,
        parent: Version | None,
    ) -> None:
        if version.object_id != object_id or version.sequence != sequence:
            raise ObjectChunkIndexHistoryError("Version identity mismatch")
        if delta.object_id != object_id or delta.sequence != sequence:
            raise ObjectChunkIndexHistoryError("VersionDelta identity mismatch")
        if delta.version_id != version.version_id:
            raise ObjectChunkIndexHistoryError("VersionDelta VersionID mismatch")
        if delta.created_at != version.created_at:
            raise ObjectChunkIndexHistoryError("Version and Delta timestamps differ")
        if self._canonicalizer.hash_delta(delta) != version.delta_root:
            raise ObjectChunkIndexHistoryError(
                f"Version delta_root mismatch at sequence {sequence}"
            )
        if sequence == 1:
            if (
                version.parent_version_id is not None
                or version.previous_version_hash is not None
            ):
                raise ObjectChunkIndexHistoryError("Version 1 parent link is invalid")
            if parent is not None:
                raise ObjectChunkIndexHistoryError("Unexpected parent before Version 1")
        else:
            if parent is None:
                raise ObjectChunkIndexHistoryError(
                    f"Missing parent Version before sequence {sequence}"
                )
            if version.parent_version_id != parent.version_id:
                raise ObjectChunkIndexHistoryError(
                    f"Parent VersionID mismatch at sequence {sequence}"
                )
            if (
                self._canonicalizer.hash_version(parent)
                != version.previous_version_hash
            ):
                raise ObjectChunkIndexHistoryError(
                    f"previous_version_hash mismatch at sequence {sequence}"
                )
        self._validate_version_signature(version)

    def _require_catalog_chunk(self, object_id: str, chunk_id: str) -> None:
        if self._chunks.get(object_id, chunk_id) is None:
            raise ObjectChunkIndexHistoryError(
                f"Chunk {chunk_id!r} is missing from Object {object_id!r} catalog"
            )

    def _validate_state_catalog(
        self, object_id: str, refs: tuple[ChunkRef, ...]
    ) -> None:
        for ref in refs:
            self._require_catalog_chunk(object_id, ref.chunk_id)

    @staticmethod
    def _validate_target_sequence(sequence: int) -> None:
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise ValueError("target_sequence must be a non-negative integer")
