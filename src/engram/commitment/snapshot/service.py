"""Application-facing operations for Snapshot commitments."""
from engram.metadata.logical.models import ChunkRef, Version

from .canonicalizer import SnapshotCanonicalizer


class SnapshotCommitmentService:
    def __init__(self, canonicalizer: SnapshotCanonicalizer | None = None) -> None:
        self._canonicalizer = canonicalizer or SnapshotCanonicalizer()

    def compute_snapshot_root(
        self,
        *,
        object_id: str,
        sequence: int,
        version_id: str,
        chunk_refs: tuple[ChunkRef, ...],
    ) -> bytes:
        return self._canonicalizer.hash(
            object_id=object_id,
            sequence=sequence,
            version_id=version_id,
            chunk_refs=chunk_refs,
        )

    def compute_for_version(
        self, version: Version, chunk_refs: tuple[ChunkRef, ...]
    ) -> bytes:
        if not isinstance(version, Version):
            raise TypeError("version must be a Version")
        return self.compute_snapshot_root(
            object_id=version.object_id,
            sequence=version.sequence,
            version_id=version.version_id,
            chunk_refs=chunk_refs,
        )

    def verify_snapshot(
        self,
        *,
        object_id: str,
        sequence: int,
        version_id: str,
        chunk_refs: tuple[ChunkRef, ...],
        expected_root: bytes,
    ) -> bool:
        if type(expected_root) is not bytes or len(expected_root) != 32:
            raise ValueError("expected_root must be exactly 32 bytes")
        actual = self.compute_snapshot_root(
            object_id=object_id,
            sequence=sequence,
            version_id=version_id,
            chunk_refs=chunk_refs,
        )
        return actual == expected_root

    def verify_for_version(
        self,
        version: Version,
        chunk_refs: tuple[ChunkRef, ...],
        expected_root: bytes,
    ) -> bool:
        if not isinstance(version, Version):
            raise TypeError("version must be a Version")
        return self.verify_snapshot(
            object_id=version.object_id,
            sequence=version.sequence,
            version_id=version.version_id,
            chunk_refs=chunk_refs,
            expected_root=expected_root,
        )
