from __future__ import annotations

from threading import Lock
from typing import Protocol

from ..models import Object, Version, VersionDelta
from ...database import MetadataDB
from ..repositories import (
    ObjectRepository,
    VersionRepository,
    VersionDeltaRepository,
)
from .canonicalizer import Canonicalizer
from .version_builder import VersionBuildResult
from .version_signer import verify_ed25519_signature


class VersionCommitError(Exception):
    """Base error raised while committing a signed Logical Version."""


class ObjectNotFoundError(VersionCommitError):
    """The target Object does not exist."""


class VersionHeadConflictError(VersionCommitError):
    """The Object Head changed since the Version was built."""


class InvalidVersionBuildResultError(VersionCommitError):
    """The VersionBuildResult is internally inconsistent or invalid."""


class PhysicalReadinessError(VersionCommitError):
    """The physical data required by this Version is not ready."""


class MetadataCommitError(VersionCommitError):
    """The atomic metadata batch could not be committed."""


class PhysicalReadinessChecker(Protocol):
    """Synchronous readiness contract supplied by Physical Metadata."""

    def is_ready(self, result: VersionBuildResult) -> bool:
        """Return True only when all physical data required by result is durable."""
        ...


class VersionCommitService:
    """
    Atomically publishes a signed Version, its Delta, and the Object Head.

    Concurrency is serialized per Object within this process. This is not a
    cross-process transaction/lock; deployments with multiple writers must add
    an external per-Object coordinator or transactional storage.
    """

    def __init__(
        self,
        db: MetadataDB,
        physical_readiness: PhysicalReadinessChecker,
        *,
        canonicalizer: Canonicalizer | None = None,
    ) -> None:
        if db is None:
            raise TypeError("db is required")
        if not callable(getattr(physical_readiness, "is_ready", None)):
            raise TypeError("physical_readiness must provide is_ready(result)")
        self._db = db
        self._objects = ObjectRepository(db)
        self._versions = VersionRepository(db)
        self._deltas = VersionDeltaRepository(db)
        self._readiness = physical_readiness
        self._canonicalizer = canonicalizer or Canonicalizer()
        self._locks_guard = Lock()
        self._object_locks: dict[str, Lock] = {}

    def _lock_for(self, object_id: str) -> Lock:
        with self._locks_guard:
            lock = self._object_locks.get(object_id)
            if lock is None:
                lock = Lock()
                self._object_locks[object_id] = lock
            return lock

    def commit(
        self,
        result: VersionBuildResult,
        *,
        expected_head_sequence: int,
    ) -> Version:
        if not isinstance(result, VersionBuildResult):
            raise TypeError("result must be a VersionBuildResult")
        if (
            isinstance(expected_head_sequence, bool)
            or not isinstance(expected_head_sequence, int)
            or expected_head_sequence < 0
        ):
            raise ValueError("expected_head_sequence must be a non-negative integer")

        version = result.version
        delta = result.delta
        if not isinstance(version, Version):
            raise InvalidVersionBuildResultError("result.version must be a Version")
        if not isinstance(delta, VersionDelta):
            raise InvalidVersionBuildResultError("result.delta must be a VersionDelta")

        with self._lock_for(version.object_id):
            obj = self._objects.get(version.object_id)
            if obj is None:
                raise ObjectNotFoundError(
                    f"Object {version.object_id!r} does not exist"
                )

            if obj.head_sequence != expected_head_sequence:
                raise VersionHeadConflictError(
                    f"Expected Head sequence {expected_head_sequence}, "
                    f"found {obj.head_sequence}"
                )

            self._validate_result(obj, result)

            try:
                ready = self._readiness.is_ready(result)
            except Exception as exc:
                raise PhysicalReadinessError("Physical readiness check failed") from exc
            if ready is not True:
                raise PhysicalReadinessError(
                    "Physical data required by this Version is not ready"
                )

            updated_object = Object(
                object_id=obj.object_id,
                head_sequence=version.sequence,
                head_version_id=version.version_id,
                created_at=obj.created_at,
                updated_at=version.created_at,
            )

            try:
                with self._db.batch() as batch:
                    self._versions.put(version, batch)
                    self._deltas.put(delta, batch)
                    self._objects.put(updated_object, batch)
                    batch.commit()
            except Exception as exc:
                raise MetadataCommitError(
                    "Failed to atomically commit Version metadata"
                ) from exc

            return version

    def _validate_result(
        self,
        obj: Object,
        result: VersionBuildResult,
    ) -> None:
        version = result.version
        delta = result.delta

        if delta.object_id != obj.object_id:
            raise InvalidVersionBuildResultError(
                "Delta object_id does not match Object"
            )
        if version.object_id != obj.object_id:
            raise InvalidVersionBuildResultError(
                "Version object_id does not match Object"
            )
        expected_sequence = obj.head_sequence + 1
        if version.sequence != expected_sequence or delta.sequence != expected_sequence:
            raise InvalidVersionBuildResultError(
                f"Version and Delta sequence must both be {expected_sequence}"
            )
        if delta.version_id != version.version_id:
            raise InvalidVersionBuildResultError(
                "VersionID does not match between Version and Delta"
            )
        if version.created_at != delta.created_at:
            raise InvalidVersionBuildResultError(
                "Version and Delta timestamps must match"
            )
        if version.created_at < obj.updated_at:
            raise InvalidVersionBuildResultError(
                "Version timestamp must not be earlier than Object.updated_at"
            )

        expected_delta_root = self._canonicalizer.hash_delta(delta)
        if version.delta_root != expected_delta_root:
            raise InvalidVersionBuildResultError(
                "Version delta_root does not match the supplied Delta"
            )

        if obj.head_sequence == 0:
            if version.parent_version_id is not None:
                raise InvalidVersionBuildResultError(
                    "First Version must not have a parent"
                )
            if version.previous_version_hash is not None:
                raise InvalidVersionBuildResultError(
                    "First Version must not have a previous Version hash"
                )
        else:
            parent = self._versions.get(obj.object_id, obj.head_sequence)
            if parent is None or parent.version_id != obj.head_version_id:
                raise InvalidVersionBuildResultError(
                    "Stored Object Head does not reference a valid parent Version"
                )
            if version.parent_version_id != parent.version_id:
                raise InvalidVersionBuildResultError(
                    "Version parent does not match current Object Head"
                )
            expected_parent_hash = self._canonicalizer.hash_version(parent)
            if version.previous_version_hash != expected_parent_hash:
                raise InvalidVersionBuildResultError(
                    "Version previous_version_hash does not match parent Version"
                )

        signing_payload = self._canonicalizer.encode_version_signing_payload(version)
        if not verify_ed25519_signature(
            version.writer_public_key,
            signing_payload,
            version.signature,
        ):
            raise InvalidVersionBuildResultError("Version Ed25519 signature is invalid")
