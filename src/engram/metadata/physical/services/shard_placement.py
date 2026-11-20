from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Callable

from ..models import (
    GenerationStatus,
    PlacementStatus,
    ReceiptType,
    ShardPlacement,
    StorageReceipt,
)
from ._availability import ShardAvailabilitySnapshot
from .storage_receipt import StorageReceiptService, StorageReceiptServiceError

if TYPE_CHECKING:
    from ...database.batch import MetadataBatch
    from ..repositories import (
        GenerationRepository,
        GenerationShardRepository,
        ShardPlacementRepository,
    )


class ShardPlacementServiceError(Exception):
    """Base error for ShardPlacement operations."""


class ShardPlacementGenerationNotFoundError(ShardPlacementServiceError):
    pass


class ShardPlacementNotFoundError(ShardPlacementServiceError):
    pass


class ShardPlacementPreconditionError(ShardPlacementServiceError):
    pass


class ShardPlacementIntegrityError(ShardPlacementServiceError):
    pass


class ShardPlacementConcurrencyError(ShardPlacementServiceError):
    pass


class ShardPlacementService:
    """Manage physical shard copies and their opaque storage evidence.

    All writes are staged into the caller-owned batch. This service does not
    perform storage I/O, receipt cryptographic verification, or shard encoding.
    """

    def __init__(
        self,
        generation_repository: GenerationRepository,
        generation_shard_repository: GenerationShardRepository,
        shard_placement_repository: ShardPlacementRepository,
        storage_receipt_service: StorageReceiptService,
        *,
        clock: Callable[[], int] | None = None,
    ) -> None:
        for name, repository in (
            ("generation_repository", generation_repository),
            ("generation_shard_repository", generation_shard_repository),
            ("shard_placement_repository", shard_placement_repository),
            ("storage_receipt_service", storage_receipt_service),
        ):
            if repository is None:
                raise TypeError(f"{name} is required")
        self._generations = generation_repository
        self._shards = generation_shard_repository
        self._placements = shard_placement_repository
        self._receipt_service = storage_receipt_service
        self._clock = clock or (lambda: __import__("time").time_ns() // 1_000_000)

    def register(
        self, placement: ShardPlacement, *, batch: MetadataBatch
    ) -> ShardPlacement:
        if not isinstance(placement, ShardPlacement):
            raise TypeError("placement must be a ShardPlacement")
        if batch is None:
            raise TypeError("batch is required")
        if placement.status is not PlacementStatus.PENDING:
            raise ShardPlacementPreconditionError("New Placement must start PENDING")
        if placement.stored_at is not None or placement.verified_at is not None:
            raise ShardPlacementPreconditionError(
                "New Placement cannot have lifecycle timestamps"
            )
        generation = self._require_generation(placement.generation_id)
        if generation.status is not GenerationStatus.STORING:
            raise ShardPlacementPreconditionError(
                "Placement can only be registered while Generation is STORING"
            )
        if self._shards.get(placement.generation_id, placement.shard_index) is None:
            raise ShardPlacementIntegrityError(
                "Placement references a missing GenerationShard"
            )
        if self._placements.get(placement.placement_id) is not None:
            raise ShardPlacementPreconditionError(
                f"Placement {placement.placement_id!r} already exists"
            )
        self._placements.put(placement, batch)
        return placement

    def get(self, placement_id: str) -> ShardPlacement | None:
        return self._placements.get(placement_id)

    def list_for_shard(
        self, generation_id: str, shard_index: int
    ) -> tuple[ShardPlacement, ...]:
        return self._placements.list_for_shard(generation_id, shard_index)

    def mark_stored(
        self,
        placement_id: str,
        receipt: StorageReceipt,
        expected_updated_at: int,
        *,
        batch: MetadataBatch,
    ) -> ShardPlacement:
        current = self._load_for_transition(
            placement_id, expected_updated_at, {PlacementStatus.PENDING}
        )
        self._require_shard(current)
        if not isinstance(receipt, StorageReceipt):
            raise TypeError("receipt must be a StorageReceipt")
        if receipt.receipt_type is not ReceiptType.STORAGE_ACK:
            raise ShardPlacementPreconditionError(
                "mark_stored requires a STORAGE_ACK receipt"
            )
        try:
            self._receipt_service.register(receipt, batch=batch)
        except StorageReceiptServiceError as exc:
            raise ShardPlacementIntegrityError(str(exc)) from exc
        now = max(self._now(), current.updated_at + 1, receipt.issued_at)
        updated = replace(
            current,
            status=PlacementStatus.STORED,
            stored_at=receipt.issued_at,
            updated_at=now,
        )
        self._placements.update(updated, batch)
        return updated

    def mark_verified(
        self,
        placement_id: str,
        receipt: StorageReceipt,
        expected_updated_at: int,
        *,
        batch: MetadataBatch,
    ) -> ShardPlacement:
        current = self._load_for_transition(
            placement_id, expected_updated_at, {PlacementStatus.STORED}
        )
        self._require_shard(current)
        if not isinstance(receipt, StorageReceipt):
            raise TypeError("receipt must be a StorageReceipt")
        if receipt.receipt_type is not ReceiptType.STORAGE_VERIFICATION:
            raise ShardPlacementPreconditionError(
                "mark_verified requires a STORAGE_VERIFICATION receipt"
            )
        receipts = self._receipt_service.list_for_placement(placement_id)
        if not any(item.receipt_type is ReceiptType.STORAGE_ACK for item in receipts):
            raise ShardPlacementIntegrityError(
                "STORED Placement has no persisted STORAGE_ACK"
            )
        try:
            self._receipt_service.register(receipt, batch=batch)
        except StorageReceiptServiceError as exc:
            raise ShardPlacementIntegrityError(str(exc)) from exc
        now = max(self._now(), current.updated_at + 1, receipt.issued_at)
        updated = replace(
            current,
            status=PlacementStatus.VERIFIED,
            verified_at=receipt.issued_at,
            updated_at=now,
        )
        self._placements.update(updated, batch)
        return updated

    def mark_failed(
        self, placement_id: str, expected_updated_at: int, *, batch: MetadataBatch
    ) -> ShardPlacement:
        current = self._load_for_transition(
            placement_id,
            expected_updated_at,
            {PlacementStatus.PENDING, PlacementStatus.STORED},
        )
        now = self._next_time(current)
        return self._stage(
            replace(current, status=PlacementStatus.FAILED, updated_at=now), batch
        )

    def mark_lost(
        self, placement_id: str, expected_updated_at: int, *, batch: MetadataBatch
    ) -> ShardPlacement:
        current = self._load_for_transition(
            placement_id,
            expected_updated_at,
            {PlacementStatus.STORED, PlacementStatus.VERIFIED},
        )
        now = self._next_time(current)
        return self._stage(
            replace(current, status=PlacementStatus.LOST, updated_at=now), batch
        )

    def get_availability_snapshot(
        self, generation_id: str
    ) -> ShardAvailabilitySnapshot:
        generation = self._require_generation(generation_id)
        shards = self._shards.list_for_generation(generation_id)
        expected = set(range(generation.total_shards))
        actual = {shard.shard_index for shard in shards}
        if actual != expected:
            raise ShardPlacementIntegrityError(
                "GenerationShard set is incomplete or contains unexpected indices"
            )
        verified = set()
        for shard in shards:
            placements = self._placements.list_for_shard(
                generation_id, shard.shard_index
            )
            if any(
                item.generation_id != generation_id
                or item.shard_index != shard.shard_index
                for item in placements
            ):
                raise ShardPlacementIntegrityError(
                    "Placement index returned an inconsistent record"
                )
            for item in placements:
                if item.status is PlacementStatus.VERIFIED:
                    # Availability is a trust boundary: never count a status flag
                    # without validating its persisted receipt evidence.
                    self.validate(item.placement_id)
                    verified.add(shard.shard_index)
        return ShardAvailabilitySnapshot(generation_id, frozenset(verified))

    def validate(self, placement_id: str) -> bool:
        placement = self._placements.get(placement_id)
        if placement is None:
            raise ShardPlacementNotFoundError(placement_id)
        self._require_generation(placement.generation_id)
        self._require_shard(placement)
        receipts = self._receipt_service.list_for_placement(placement_id)
        ack = [r for r in receipts if r.receipt_type is ReceiptType.STORAGE_ACK]
        verification = [
            r for r in receipts if r.receipt_type is ReceiptType.STORAGE_VERIFICATION
        ]
        if (
            placement.status
            in {PlacementStatus.STORED, PlacementStatus.VERIFIED, PlacementStatus.LOST}
            and not ack
        ):
            raise ShardPlacementIntegrityError(
                "Placement requires a STORAGE_ACK receipt"
            )
        if placement.status is PlacementStatus.VERIFIED and not verification:
            raise ShardPlacementIntegrityError(
                "VERIFIED Placement requires a STORAGE_VERIFICATION receipt"
            )
        if placement.stored_at is not None and not any(
            r.issued_at == placement.stored_at for r in ack
        ):
            raise ShardPlacementIntegrityError(
                "stored_at does not match a STORAGE_ACK receipt"
            )
        if placement.verified_at is not None and not any(
            r.issued_at == placement.verified_at for r in verification
        ):
            raise ShardPlacementIntegrityError(
                "verified_at does not match a STORAGE_VERIFICATION receipt"
            )
        return True

    def validate_generation(self, generation_id: str) -> bool:
        generation = self._require_generation(generation_id)
        shards = self._shards.list_for_generation(generation_id)
        if {s.shard_index for s in shards} != set(range(generation.total_shards)):
            raise ShardPlacementIntegrityError(
                "GenerationShard set is incomplete or invalid"
            )
        for shard in shards:
            for placement in self._placements.list_for_shard(
                generation_id, shard.shard_index
            ):
                self.validate(placement.placement_id)
        return True

    def _require_generation(self, generation_id):
        generation = self._generations.get(generation_id)
        if generation is None:
            raise ShardPlacementGenerationNotFoundError(generation_id)
        return generation

    def _require_shard(self, placement):
        shard = self._shards.get(placement.generation_id, placement.shard_index)
        if shard is None:
            raise ShardPlacementIntegrityError(
                "Placement references a missing GenerationShard"
            )
        return shard

    def _load_for_transition(self, placement_id, expected_updated_at, allowed):
        current = self._placements.get(placement_id)
        if current is None:
            raise ShardPlacementNotFoundError(placement_id)
        if current.updated_at != expected_updated_at:
            raise ShardPlacementConcurrencyError(
                "Placement was modified by another operation"
            )
        if current.status not in allowed:
            raise ShardPlacementPreconditionError(
                f"Cannot transition Placement from {current.status.value}"
            )
        return current

    def _now(self):
        value = self._clock()
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("clock must return a non-negative integer timestamp")
        return value

    def _next_time(self, current):
        return max(self._now(), current.updated_at + 1)

    def _stage(self, placement, batch):
        if batch is None:
            raise TypeError("batch is required")
        self._placements.update(placement, batch)
        return placement
