"""Small structural contracts shared by incremental-storage mixins.

The mixins inherit the protocols they consume so static type checkers can
resolve cross-mixin attributes and methods without annotating every ``self``.
Protocol bases are placed after concrete mixins in the store's MRO, so their
stub methods never replace the actual implementations at runtime.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from engram.storage import ChunkCommitment, StorageManager
from engram.erasure_coding import EncodedShards

from .types import GenerationRecord, ObjectState, StagedGeneration, VersionEntry


class StateAccess(Protocol):
    """Persistence and metadata operations supplied by ``StateMixin``."""

    state_dir: Path
    STATE_VERSION: int

    @staticmethod
    def _object_key(object_id: str) -> str: ...
    def _state_path(self, object_id: str) -> Path: ...
    @staticmethod
    def _atomic_write(path: Path, payload: bytes) -> None: ...
    def _save(self, object_id: str, state: ObjectState) -> None: ...
    def _load(self, object_id: str, *, create: bool = False) -> ObjectState: ...
    @staticmethod
    def _b64(data: bytes) -> str: ...
    @staticmethod
    def _unb64(value: str) -> bytes: ...
    @staticmethod
    def _chunk_root(data: bytes, chunk_size: int) -> str: ...
    def _chunk_layout(self, data: bytes) -> list[int]: ...


class BufferAccess(Protocol):
    """Temporary buffer and staging-file operations supplied by BufferMixin."""

    buffer_dir: Path
    staging_dir: Path

    def _buffer_path(self, object_id: str) -> Path: ...
    def _staging_payload_path(self, object_id: str) -> Path: ...
    def _read_buffer(self, object_id: str, state: ObjectState) -> bytes: ...
    def _write_buffer(self, object_id: str, data: bytes) -> None: ...
    def _write_staging_payload(self, object_id: str, ciphertext: bytes) -> None: ...
    def _read_staging_payload(self, object_id: str) -> bytes: ...
    @staticmethod
    def _remove_if_exists(path: Path) -> None: ...
    def _cleanup_committed_temporary_files(
        self, object_id: str, state: ObjectState
    ) -> None: ...


class GenerationAccess(Protocol):
    """Generation lifecycle operations supplied by GenerationMixin."""

    storage: StorageManager
    data_shards: int
    total_shards: int
    chunk_size: int
    max_wait_seconds: float

    def _seal(self, object_id: str, state: ObjectState) -> None: ...
    def _finish_seal(self, object_id: str, state: ObjectState) -> None: ...
    def _encrypt_generation(
        self,
        object_id: str,
        state: ObjectState,
        plaintext: bytes,
        entries: list[VersionEntry],
        generation_id: str,
        start_version: int,
        end_version: int,
        chunk_root: str,
    ) -> None: ...
    def _encode_generation(self, ciphertext: bytes) -> EncodedShards: ...
    def _commit_generation(self, encoded: EncodedShards) -> ChunkCommitment: ...
    def _write_generation(
        self, generation_id: str, encoded: EncodedShards, commitment: ChunkCommitment
    ) -> None: ...
    def _publish_generation_state(
        self, object_id: str, state: ObjectState, staged: StagedGeneration
    ) -> None: ...
    def _verify_generation(
        self,
        object_id: str,
        state: ObjectState,
        staged: StagedGeneration,
        ciphertext: bytes,
    ) -> bytes: ...
    def _read_generation_plaintext(
        self, state: ObjectState, generation: GenerationRecord
    ) -> bytes: ...
    def _validate_staged_generation(
        self, state: ObjectState, staged: StagedGeneration, plaintext: bytes
    ) -> None: ...
    def recover(self, object_id: str) -> ObjectState: ...


class VersionAccess(Protocol):
    """Version/history operations supplied by VersionMixin."""

    @staticmethod
    def _generation_entries(generation: GenerationRecord) -> list[VersionEntry]: ...
    def _ordered_generations(self, state: ObjectState) -> list[GenerationRecord]: ...
    def _validate_version_generation_mapping(
        self,
        state: ObjectState,
        generations: list[GenerationRecord],
    ) -> tuple[dict[int, str], list[int]]: ...
    def list_versions(self, object_id: str) -> list[VersionEntry]: ...
    def list_generations(self, object_id: str) -> list[GenerationRecord]: ...
    def get_version(self, object_id: str, version: int) -> bytes: ...
    def get_mmr_root(self, object_id: str) -> str: ...
    def get_inclusion_proof(
        self, object_id: str, version: int
    ) -> dict[str, object]: ...
    def get_consistency_proof(
        self, object_id: str, old_size: int
    ) -> dict[str, object]: ...
    def verify_history(self, object_id: str) -> bool: ...
