"""Canonical immutable manifests for version-history MMR leaves."""
from __future__ import annotations

import hashlib
from typing import Any

from engram.mmr.hashing import canonical_manifest_bytes
from .types import GenerationRecord, VersionEntry

GENERATION_COMMITMENT_DOMAIN = b"ENGRAM_GENERATION_COMMITMENT_V1"


def generation_commitment(generation: GenerationRecord) -> str:
    """Commit only stable generation metadata, excluding mutable/runtime fields."""
    payload = {
        "generation_id": generation["generation_id"],
        "start_version": int(generation["start_version"]),
        "end_version": int(generation["end_version"]),
        "chunk_root": generation["chunk_root"],
        "plaintext_sha256": generation["plaintext_sha256"],
        "plaintext_size_bytes": int(generation["plaintext_size_bytes"]),
    }
    return hashlib.sha256(
        GENERATION_COMMITMENT_DOMAIN + canonical_manifest_bytes(payload)
    ).hexdigest()


def build_version_manifest(
    object_id: str,
    entry: VersionEntry,
    generation: GenerationRecord,
) -> dict[str, Any]:
    """Build the deterministic manifest committed for one Available version."""
    version = int(entry["version"])
    if version < 1:
        raise ValueError("Version numbers must be positive")
    generation_id = entry.get("generation_id")
    if generation_id not in (None, generation["generation_id"]):
        raise ValueError(f"Version {version} generation_id mismatch")
    if not int(generation["start_version"]) <= version <= int(generation["end_version"]):
        raise ValueError(f"Version {version} is outside its generation range")
    return {
        "schema_version": 1,
        "object_id": object_id,
        "version": version,
        "parent_version": version - 1 if version > 1 else None,
        "operation": "append",
        "delta_size_bytes": int(entry["delta_size_bytes"]),
        "delta_sha256": entry["delta_sha256"],
        "generation_id": generation["generation_id"],
        "generation_commitment": generation_commitment(generation),
    }
