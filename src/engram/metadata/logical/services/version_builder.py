from dataclasses import dataclass
from ..models import Object, Version, VersionDelta
from ..models._validation import validate_non_empty_string
from ..models.unsigned_version import UnsignedVersion

from .canonicalizer import Canonicalizer
from .version_signer import VersionSigner

DEFAULT_VERSION_CODEC = "ENGRAM-LOGICAL-METADATA/1"


class VersionBuildError(Exception):
    """Base error raised while building a Version."""


class VersionHeadMismatchError(VersionBuildError):
    """The supplied parent does not match the Object's current Head."""


class VersionDeltaMismatchError(VersionBuildError):
    """The supplied Delta does not describe the next Version of the Object."""


class VersionSigningError(VersionBuildError):
    """The signer failed or returned an invalid signature."""


@dataclass(frozen=True, slots=True)
class VersionBuildResult:
    version: Version
    delta: VersionDelta


class VersionBuilder:
    """
    Builds a signed Version from a Delta already produced by SequenceDiffPlanner.

    This service is pure with respect to persistence and physical storage:
    it does not read/write repositories or check physical readiness.
    """

    def __init__(
        self,
        canonicalizer: Canonicalizer | None = None,
        *,
        codec: str = DEFAULT_VERSION_CODEC,
    ) -> None:
        validate_non_empty_string(codec, "codec")
        self._canonicalizer = canonicalizer or Canonicalizer()
        self._codec = codec

    def build(
        self,
        *,
        object: Object,
        parent_version: Version | None,
        delta: VersionDelta,
        signer: VersionSigner,
    ) -> VersionBuildResult:
        self._validate_inputs(object, parent_version, delta, signer)

        if delta.created_at < object.created_at:
            raise VersionDeltaMismatchError(
                "Delta timestamp must not be earlier than Object creation"
            )

        expected_sequence = object.head_sequence + 1
        if delta.sequence != expected_sequence:
            raise VersionDeltaMismatchError(
                f"Delta sequence must be {expected_sequence}, got {delta.sequence}"
            )

        if delta.object_id != object.object_id:
            raise VersionDeltaMismatchError(
                "Delta object_id does not match the target Object"
            )

        if object.head_sequence == 0:
            if parent_version is not None:
                raise VersionHeadMismatchError(
                    "A new Object must not have a parent Version"
                )
            parent_version_id = None
            previous_version_hash = None
        else:
            if parent_version is None:
                raise VersionHeadMismatchError(
                    "A parent Version is required when Object already has a Head"
                )
            if parent_version.object_id != object.object_id:
                raise VersionHeadMismatchError(
                    "Parent Version belongs to a different Object"
                )
            if parent_version.sequence != object.head_sequence:
                raise VersionHeadMismatchError(
                    "Parent Version sequence does not match Object Head"
                )
            if parent_version.version_id != object.head_version_id:
                raise VersionHeadMismatchError(
                    "Parent Version ID does not match Object Head"
                )
            if delta.created_at < parent_version.created_at:
                raise VersionDeltaMismatchError(
                    "Delta timestamp must not be earlier than its parent Version"
                )
            parent_version_id = parent_version.version_id
            previous_version_hash = self._canonicalizer.hash_version(parent_version)

        # SequenceDiffPlanner assigned this ID to the Delta; the Version
        # must use exactly the same ID.
        unsigned = UnsignedVersion(
            version_id=delta.version_id,
            object_id=object.object_id,
            sequence=expected_sequence,
            parent_version_id=parent_version_id,
            delta_root=self._canonicalizer.hash_delta(delta),
            previous_version_hash=previous_version_hash,
            codec=self._codec,
            writer_public_key=signer.public_key,
            created_at=delta.created_at,
        )

        try:
            signing_payload = self._canonicalizer.encode_version_signing_payload(
                unsigned
            )
            signature = signer.sign(signing_payload)
        except Exception as exc:
            raise VersionSigningError("Failed to sign Version payload") from exc

        if not isinstance(signature, bytes) or not signature:
            raise VersionSigningError("Signer must return a non-empty bytes signature")

        version = Version(
            version_id=unsigned.version_id,
            object_id=unsigned.object_id,
            sequence=unsigned.sequence,
            parent_version_id=unsigned.parent_version_id,
            delta_root=unsigned.delta_root,
            previous_version_hash=unsigned.previous_version_hash,
            codec=unsigned.codec,
            writer_public_key=unsigned.writer_public_key,
            signature=signature,
            created_at=unsigned.created_at,
        )

        return VersionBuildResult(version=version, delta=delta)

    @staticmethod
    def _validate_inputs(
        object: Object,
        parent_version: Version | None,
        delta: VersionDelta,
        signer: VersionSigner,
    ) -> None:
        if not isinstance(object, Object):
            raise TypeError("object must be an Object")
        if parent_version is not None and not isinstance(parent_version, Version):
            raise TypeError("parent_version must be a Version or None")
        if not isinstance(delta, VersionDelta):
            raise TypeError("delta must be a VersionDelta")
        if not callable(getattr(signer, "sign", None)) or not hasattr(
            signer, "public_key"
        ):
            raise TypeError("signer must provide public_key and sign(payload)")

        public_key = signer.public_key
        if not isinstance(public_key, bytes) or not public_key:
            raise TypeError("signer.public_key must be non-empty bytes")
