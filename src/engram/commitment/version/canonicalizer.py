"""Canonical encoding for Version Manifest commitments."""

from uuid import UUID

from engram.commitment.common import (
    VERSION_MANIFEST,
    encode_optional,
    encode_raw_bytes,
    encode_string,
    encode_u64,
    encode_uuid,
    hash_with_domain,
)
from engram.commitment.common.exceptions import InvalidInputError
from engram.metadata.logical.models import Version
from engram.metadata.logical.models.unsigned_version import UnsignedVersion


def _uuid_from_model(value: str, field: str) -> UUID:
    if type(value) is not str:
        raise TypeError(f"{field} must be a UUID string in the Metadata model")
    try:
        return UUID(value)
    except (ValueError, AttributeError) as exc:
        raise InvalidInputError(f"{field} must be a valid UUID") from exc


class VersionCanonicalizer:
    """Version Manifest excludes writer key, signature and all timestamps."""

    @classmethod
    def encode(cls, version: Version | UnsignedVersion) -> bytes:
        if not isinstance(version, (Version, UnsignedVersion)):
            raise TypeError("version must be a Version or UnsignedVersion")
        parent = encode_optional(
            version.parent_version_id,
            lambda value: encode_uuid(_uuid_from_model(value, "parent_version_id")),
        )
        previous_hash = encode_optional(version.previous_version_hash, encode_raw_bytes)
        if len(version.delta_root) != 32:
            raise ValueError("delta_root must contain exactly 32 bytes")
        if (
            version.previous_version_hash is not None
            and len(version.previous_version_hash) != 32
        ):
            raise ValueError("previous_version_hash must contain exactly 32 bytes")
        return b"".join(
            (
                encode_string(version.object_id),
                encode_uuid(_uuid_from_model(version.version_id, "version_id")),
                encode_u64(version.sequence),
                parent,
                encode_raw_bytes(version.delta_root),
                previous_hash,
                encode_string(version.codec),
            )
        )

    @classmethod
    def hash(cls, version: Version | UnsignedVersion) -> bytes:
        return hash_with_domain(VERSION_MANIFEST, cls.encode(version))
