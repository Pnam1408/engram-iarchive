"""Typed metadata structures persisted by the incremental object store."""

from __future__ import annotations

from typing import Literal, TypedDict

from engram.mmr.types import MMRState

VersionStatus = Literal["PENDING", "Available"]


OperationType = Literal["append", "insert", "delete"]


class VersionEntry(TypedDict):
    version: int
    operation: OperationType
    offset: int | None
    delete_length: int
    delta_size_bytes: int
    delta_sha256: str
    storage_size_bytes: int
    generation_id: str | None
    status: VersionStatus


class OpenGeneration(TypedDict):
    started_at: float | None
    buffer_size: int
    entries: list[VersionEntry]


class StagedGeneration(TypedDict):
    generation_id: str
    start_version: int
    end_version: int
    chunk_root: str
    plaintext_sha256: str
    plaintext_size_bytes: int
    nonce_b64: str
    ciphertext_size_bytes: int
    entries: list[VersionEntry]


class GenerationRecord(TypedDict):
    generation_id: str
    start_version: int
    end_version: int
    chunk_root: str
    chunk_size: int
    plaintext_sha256: str
    plaintext_size_bytes: int
    nonce_b64: str
    entries: list[VersionEntry]
    data_shards: int
    total_shards: int
    status: Literal["Available"]


class ObjectState(TypedDict):
    schema_version: int
    object_id: str
    key_b64: str
    next_version: int
    versions: list[VersionEntry]
    generations: list[GenerationRecord]
    open: OpenGeneration
    sealing: StagedGeneration | None
    mmr: MMRState


class LegacyOpenBuffer(TypedDict, total=False):
    """Fields read while migrating schema-v1 open-buffer metadata."""

    buffer_b64: str
    buffer_size: int


class LegacyStagedPayload(TypedDict, total=False):
    """Legacy inline ciphertext field removed by schema-v2 migration."""

    ciphertext_b64: str
