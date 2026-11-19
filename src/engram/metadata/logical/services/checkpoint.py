from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4
from threading import Lock

from ..models import Checkpoint, CheckpointEntry, ChunkRef, Version
from ..repositories import (
    ObjectRepository,
    VersionRepository,
    ObjectChunkIndexRepository,
    CheckpointRepository,
    CheckpointEntryRepository,
    ChunkRepository,
)
from ...database import MetadataDB
from ...repositories import CorruptMetadataError
from .canonicalizer import Canonicalizer


class CheckpointError(Exception):
    """Base Checkpoint lifecycle error."""


class CheckpointHeadMismatchError(CheckpointError):
    """Object Head and materialized index do not describe the same Version."""


class CheckpointVerificationError(CheckpointError):
    """Checkpoint snapshot failed integrity or reference verification."""


class CheckpointConcurrentUpdateError(CheckpointError):
    """Object changed while a Checkpoint snapshot was being captured."""


@dataclass(frozen=True, slots=True)
class CheckpointBuildResult:
    checkpoint: Checkpoint
    entries: tuple[CheckpointEntry, ...]


class CheckpointBuilder:
    """Pure builder that converts an ordered ChunkRef state into a Checkpoint."""

    def __init__(self, canonicalizer: Canonicalizer | None = None) -> None:
        self._canonicalizer = canonicalizer or Canonicalizer()

    def build(
        self,
        *,
        object_id: str,
        sequence: int,
        version_id: str,
        refs: tuple[ChunkRef, ...],
        created_at: int,
        checkpoint_id: str | None = None,
    ) -> CheckpointBuildResult:
        if not isinstance(refs, tuple) or any(
            not isinstance(ref, ChunkRef) for ref in refs
        ):
            raise TypeError("refs must be a tuple of ChunkRef")
        ordered = tuple(sorted(refs, key=lambda ref: ref.order_key))
        if len({ref.chunk_ref_id for ref in ordered}) != len(ordered):
            raise ValueError("refs contain duplicate ChunkRefID")
        if len({ref.order_key for ref in ordered}) != len(ordered):
            raise ValueError("refs contain duplicate OrderKey")
        cp_id = checkpoint_id or str(uuid4())
        entries = tuple(
            CheckpointEntry(
                checkpoint_id=cp_id,
                chunk_ref_id=ref.chunk_ref_id,
                chunk_id=ref.chunk_id,
                order_key=ref.order_key,
            )
            for ref in ordered
        )
        root = self._canonicalizer.hash_snapshot(
            object_id=object_id,
            sequence=sequence,
            version_id=version_id,
            entries=entries,
        )
        checkpoint = Checkpoint(
            checkpoint_id=cp_id,
            object_id=object_id,
            sequence=sequence,
            version_id=version_id,
            entry_count=len(entries),
            snapshot_root=root,
            created_at=created_at,
        )
        return CheckpointBuildResult(checkpoint, entries)


class CheckpointVerifier:
    """Verifies Checkpoint snapshot commitment and optional Version binding."""

    def __init__(self, canonicalizer: Canonicalizer | None = None) -> None:
        self._canonicalizer = canonicalizer or Canonicalizer()

    def verify(
        self,
        checkpoint: Checkpoint,
        entries: tuple[CheckpointEntry, ...],
        *,
        version: Version | None = None,
    ) -> None:
        if not isinstance(checkpoint, Checkpoint):
            raise TypeError("checkpoint must be a Checkpoint")
        if not isinstance(entries, tuple) or any(
            not isinstance(e, CheckpointEntry) for e in entries
        ):
            raise TypeError("entries must be a tuple of CheckpointEntry")
        if len(entries) != checkpoint.entry_count:
            raise CheckpointVerificationError("Checkpoint entry_count mismatch")
        if any(e.checkpoint_id != checkpoint.checkpoint_id for e in entries):
            raise CheckpointVerificationError(
                "CheckpointEntry belongs to another Checkpoint"
            )
        if version is not None:
            if (
                version.object_id != checkpoint.object_id
                or version.sequence != checkpoint.sequence
                or version.version_id != checkpoint.version_id
            ):
                raise CheckpointVerificationError(
                    "Checkpoint does not reference the supplied Version"
                )
        try:
            root = self._canonicalizer.hash_snapshot(
                object_id=checkpoint.object_id,
                sequence=checkpoint.sequence,
                version_id=checkpoint.version_id,
                entries=entries,
            )
        except Exception as exc:
            raise CheckpointVerificationError(
                "Cannot canonicalize Checkpoint snapshot"
            ) from exc
        if root != checkpoint.snapshot_root:
            raise CheckpointVerificationError("Checkpoint snapshot_root mismatch")


class CheckpointService:
    """Creates and verifies durable Checkpoints for the current Object Head."""

    def __init__(
        self,
        db: MetadataDB,
        *,
        builder: CheckpointBuilder | None = None,
        verifier: CheckpointVerifier | None = None,
        canonicalizer: Canonicalizer | None = None,
        verify_chunk_catalog: bool = True,
    ) -> None:
        if db is None:
            raise TypeError("db is required")
        self._db = db
        self._objects = ObjectRepository(db)
        self._versions = VersionRepository(db)
        self._index = ObjectChunkIndexRepository(db)
        self._checkpoints = CheckpointRepository(db)
        self._entries = CheckpointEntryRepository(db)
        self._chunks = ChunkRepository(db)
        self._canonicalizer = canonicalizer or Canonicalizer()
        self._builder = builder or CheckpointBuilder(self._canonicalizer)
        self._verifier = verifier or CheckpointVerifier(self._canonicalizer)
        self._verify_chunk_catalog = verify_chunk_catalog
        self._locks_guard = Lock()
        self._locks: dict[str, Lock] = {}

    def _lock_for(self, object_id: str) -> Lock:
        with self._locks_guard:
            if object_id not in self._locks:
                self._locks[object_id] = Lock()
            return self._locks[object_id]

    def create_for_head(self, object_id: str, *, created_at: int) -> Checkpoint:
        with self._lock_for(object_id):
            obj_before = self._objects.get(object_id)
            if obj_before is None:
                raise CheckpointHeadMismatchError(
                    f"Object {object_id!r} does not exist"
                )
            if obj_before.head_sequence <= 0 or obj_before.head_version_id is None:
                raise CheckpointHeadMismatchError(
                    "Cannot checkpoint an Object without a Version"
                )

            version = self._versions.get(object_id, obj_before.head_sequence)
            if version is None or version.version_id != obj_before.head_version_id:
                raise CheckpointHeadMismatchError(
                    "Object Head does not reference a valid Version"
                )
            if created_at < version.created_at:
                raise ValueError(
                    "Checkpoint created_at must not precede Version.created_at"
                )

            index_sequence_before = self._index.get_sequence(object_id)
            if index_sequence_before != obj_before.head_sequence:
                raise CheckpointHeadMismatchError(
                    "Object Chunk Index is not synchronized with Object Head"
                )
            refs = self._index.list_for_object(object_id)
            index_sequence_after = self._index.get_sequence(object_id)
            obj_after = self._objects.get(object_id)
            if obj_after != obj_before or index_sequence_after != index_sequence_before:
                raise CheckpointConcurrentUpdateError(
                    "Object changed while capturing Checkpoint snapshot"
                )

            if self._verify_chunk_catalog:
                for ref in refs:
                    chunk = self._chunks.get(object_id, ref.chunk_id)
                    if chunk is None:
                        raise CheckpointVerificationError(
                            f"Chunk {ref.chunk_id!r} is missing from Chunk Catalog"
                        )

            built = self._builder.build(
                object_id=object_id,
                sequence=obj_before.head_sequence,
                version_id=version.version_id,
                refs=refs,
                created_at=created_at,
            )
            self._verifier.verify(built.checkpoint, built.entries, version=version)

            try:
                with self._db.batch() as batch:
                    self._checkpoints.put(built.checkpoint, batch)
                    self._entries.put_all(built.checkpoint, built.entries, batch)
                    batch.commit()
            except Exception:
                raise
            return built.checkpoint

    def verify(self, checkpoint: Checkpoint) -> tuple[CheckpointEntry, ...]:
        stored = self._checkpoints.get(checkpoint.object_id, checkpoint.sequence)
        if stored is None or stored != checkpoint:
            raise CheckpointVerificationError("Checkpoint is not the stored record")
        version = self._versions.get(checkpoint.object_id, checkpoint.sequence)
        if version is None:
            raise CheckpointVerificationError("Checkpoint Version is missing")
        entries = self._entries.list_for_checkpoint(checkpoint)
        self._verifier.verify(checkpoint, entries, version=version)
        if self._verify_chunk_catalog:
            for entry in entries:
                chunk = self._chunks.get(checkpoint.object_id, entry.chunk_id)
                if chunk is None:
                    raise CheckpointVerificationError(
                        f"Chunk {entry.chunk_id!r} is missing from Chunk Catalog"
                    )
        return entries

    def latest_valid_before(
        self,
        object_id: str,
        max_sequence: int,
    ) -> tuple[Checkpoint, tuple[CheckpointEntry, ...]] | None:
        candidates = self._checkpoints.list_for_object(object_id)
        for checkpoint in reversed(candidates):
            if checkpoint.sequence > max_sequence:
                continue
            try:
                entries = self.verify(checkpoint)
                return checkpoint, entries
            except (CheckpointError, CorruptMetadataError):
                continue
        return None
