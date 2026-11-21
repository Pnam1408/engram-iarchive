"""Application-facing operations for Version and Delta commitments."""

from engram.commitment.common.exceptions import InvalidInputError
from engram.metadata.logical.models import Version, VersionDelta
from engram.metadata.logical.models.unsigned_version import UnsignedVersion

from .canonicalizer import VersionCanonicalizer
from .delta_canonicalizer import DeltaCanonicalizer


class VersionCommitmentService:
    def __init__(
        self,
        *,
        version_canonicalizer: VersionCanonicalizer | None = None,
        delta_canonicalizer: DeltaCanonicalizer | None = None,
    ) -> None:
        self._versions = version_canonicalizer or VersionCanonicalizer()
        self._deltas = delta_canonicalizer or DeltaCanonicalizer()

    def compute_delta_root(self, delta: VersionDelta) -> bytes:
        return self._deltas.hash(delta)

    def compute_manifest_hash(self, version: Version | UnsignedVersion) -> bytes:
        return self._versions.hash(version)

    def verify_manifest_hash(self, version: Version, expected_hash: bytes) -> bool:
        if type(expected_hash) is not bytes or len(expected_hash) != 32:
            raise ValueError("expected_hash must be exactly 32 bytes")
        return self.compute_manifest_hash(version) == expected_hash

    def verify_delta(self, version: Version, delta: VersionDelta) -> bool:
        if not isinstance(version, Version):
            raise TypeError("version must be a Version")
        if not isinstance(delta, VersionDelta):
            raise TypeError("delta must be a VersionDelta")
        if (version.object_id, version.version_id, version.sequence) != (
            delta.object_id,
            delta.version_id,
            delta.sequence,
        ):
            raise InvalidInputError("Version and Delta identity fields do not match")
        return self.compute_delta_root(delta) == version.delta_root
