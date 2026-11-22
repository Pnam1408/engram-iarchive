from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..models import (
    GenerationShardCommitment,
    GenerationShardCommitmentEntry,
    GenerationStatus,
    SHARD_COMMITMENT_PROTOCOL,
    BN254_SCALAR_MODULUS,
)

if TYPE_CHECKING:
    from ...database.batch import MetadataBatch
    from ..repositories import GenerationRepository, GenerationShardRepository
    from ..repositories import (
        GenerationShardCommitmentRepository,
        GenerationShardCommitmentEntryRepository,
    )


class ShardCommitmentMetadataServiceError(Exception):
    """Base error for Shard Commitment Metadata operations."""


class ShardCommitmentGenerationNotFoundError(ShardCommitmentMetadataServiceError):
    pass


class ShardCommitmentAlreadyRegisteredError(ShardCommitmentMetadataServiceError):
    pass


class ShardCommitmentPreconditionError(ShardCommitmentMetadataServiceError):
    pass


class ShardCommitmentNotFoundError(ShardCommitmentMetadataServiceError):
    pass


class ShardCommitmentIntegrityError(ShardCommitmentMetadataServiceError):
    pass


@dataclass(frozen=True, slots=True)
class GenerationShardCommitmentBundle:
    commitment: GenerationShardCommitment
    entries: tuple[GenerationShardCommitmentEntry, ...]


class ShardCommitmentMetadataService:
    """Persist and validate immutable Generation-level shard commitment metadata.



    Does not hash shard bytes, build Merkle trees, verify cryptographic proofs,
    manage Generation lifecycle, or commit caller-owned batches.
    """

    def __init__(
        self,
        generation_repository,
        shard_repository,
        commitment_repository,
        entry_repository,
    ):
        for name, value in (
            ("generation_repository", generation_repository),
            ("shard_repository", shard_repository),
            ("commitment_repository", commitment_repository),
            ("entry_repository", entry_repository),
        ):
            if value is None:
                raise TypeError(f"{name} is required")
        self._generations = generation_repository
        self._shards = shard_repository
        self._commitments = commitment_repository
        self._entries = entry_repository

    @staticmethod
    def _field_to_bytes(value: int, field_name: str) -> bytes:
        if type(value) is not int:
            raise TypeError(f"{field_name} must be an int (bool is not accepted)")
        if not 0 <= value < BN254_SCALAR_MODULUS:
            raise ValueError(f"{field_name} is outside BN254 scalar field")
        return value.to_bytes(32, "big")

    def register(
        self,
        generation_id: str,
        commitment,
        *,
        batch: MetadataBatch,
        created_at: int | None = None,
    ) -> GenerationShardCommitmentBundle:
        from engram.commitment.shard_merkle.service import ShardCommitment

        if not isinstance(generation_id, str) or not generation_id.strip():
            raise ValueError("generation_id must be a non-empty string")
        if not isinstance(commitment, ShardCommitment):
            raise TypeError("commitment must be a ShardCommitment")
        if batch is None:
            raise TypeError("batch is required")
        if created_at is None:
            created_at = int(time.time())
        generation = self._generations.get(generation_id)
        if generation is None:
            raise ShardCommitmentGenerationNotFoundError(generation_id)
        if generation.status is not GenerationStatus.ENCODED:
            raise ShardCommitmentPreconditionError(
                "Shard Commitment registration requires Generation ENCODED"
            )
        if commitment.shard_count != generation.total_shards:
            raise ShardCommitmentPreconditionError(
                "Commitment shard_count does not match Generation.total_shards"
            )
        catalog = self._shards.list_for_generation(generation_id)
        expected_indices = tuple(range(generation.total_shards))
        if tuple(s.shard_index for s in catalog) != expected_indices:
            raise ShardCommitmentPreconditionError(
                "Generation shard metadata must contain complete ordered indices"
            )
        if any(s.generation_id != generation_id for s in catalog):
            raise ShardCommitmentPreconditionError(
                "Generation shard catalog contains foreign shard records"
            )
        if self._commitments.get(
            generation_id
        ) is not None or self._entries.list_for_generation(generation_id):
            raise ShardCommitmentAlreadyRegisteredError(
                f"Shard Commitment already exists for {generation_id}"
            )
        try:
            global_root = self._field_to_bytes(commitment.global_root, "global_root")
            roots = tuple(
                self._field_to_bytes(root, f"shard_roots[{i}]")
                for i, root in enumerate(commitment.shard_roots)
            )
            if len(roots) != generation.total_shards:
                raise ValueError("shard_roots length does not match shard_count")
            record = GenerationShardCommitment(
                generation_id=generation_id,
                protocol=SHARD_COMMITMENT_PROTOCOL,
                shard_count=commitment.shard_count,
                global_root=global_root,
                created_at=created_at,
            )
            entries = tuple(
                GenerationShardCommitmentEntry(generation_id, i, root)
                for i, root in enumerate(roots)
            )
        except (TypeError, ValueError) as exc:
            raise ShardCommitmentPreconditionError(
                "Commitment roots are invalid"
            ) from exc
        # All validations/preflight checks precede staging writes. Caller must
        # discard the batch if a repository failure occurs after staging begins.
        self._commitments.put(record, batch)
        self._entries.put_all(entries, batch)
        return GenerationShardCommitmentBundle(record, entries)

    def get(self, generation_id: str) -> GenerationShardCommitment | None:
        return self._commitments.get(generation_id)

    def get_shard_root(
        self, generation_id: str, shard_index: int
    ) -> GenerationShardCommitmentEntry | None:
        return self._entries.get(generation_id, shard_index)

    def list_shard_roots(
        self, generation_id: str
    ) -> tuple[GenerationShardCommitmentEntry, ...]:
        return self._entries.list_for_generation(generation_id)

    def get_bundle(self, generation_id: str) -> GenerationShardCommitmentBundle | None:
        record = self._commitments.get(generation_id)
        if record is None:
            return None
        entries = self._entries.list_for_generation(generation_id)
        return GenerationShardCommitmentBundle(record, entries)

    def validate(self, generation_id: str) -> bool:
        generation = self._generations.get(generation_id)
        if generation is None:
            raise ShardCommitmentGenerationNotFoundError(generation_id)
        record = self._commitments.get(generation_id)
        if record is None:
            raise ShardCommitmentNotFoundError(
                f"No commitment record for {generation_id}"
            )
        entries = self._entries.list_for_generation(generation_id)
        expected = tuple(range(generation.total_shards))
        actual = tuple(e.shard_index for e in entries)
        if (
            record.shard_count != generation.total_shards
            or actual != expected
            or len(entries) != record.shard_count
        ):
            raise ShardCommitmentIntegrityError(
                "Commitment shard count/entries are incomplete or inconsistent"
            )
        if record.protocol != SHARD_COMMITMENT_PROTOCOL:
            raise ShardCommitmentIntegrityError("Unsupported commitment protocol")
        if any(e.generation_id != generation_id for e in entries):
            raise ShardCommitmentIntegrityError(
                "Commitment entry belongs to another Generation"
            )
        catalog = self._shards.list_for_generation(generation_id)
        if tuple(s.shard_index for s in catalog) != expected:
            raise ShardCommitmentIntegrityError(
                "Generation shard catalog is incomplete"
            )
        # Structural validation only; cryptographic recomputation of Global Root
        # belongs to the Commitment subsystem, not Metadata.
        return True
