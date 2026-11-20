from __future__ import annotations

from typing import TYPE_CHECKING

from ..models import ReceiptType, StorageReceipt

if TYPE_CHECKING:
    from ...database.batch import MetadataBatch
    from ..repositories import (
        GenerationShardRepository,
        ShardPlacementRepository,
        StorageReceiptRepository,
    )


class StorageReceiptServiceError(Exception):
    """Base error for StorageReceipt operations."""


class StorageReceiptPlacementNotFoundError(StorageReceiptServiceError):
    pass


class StorageReceiptNotFoundError(StorageReceiptServiceError):
    pass


class StorageReceiptIntegrityError(StorageReceiptServiceError):
    pass


class StorageReceiptAlreadyExistsError(StorageReceiptServiceError):
    pass


class StorageReceiptService:
    """Append-only management and structural validation of storage receipts.

    Receipt payloads remain opaque. This service validates only the metadata
    relationship between a receipt, its ShardPlacement, and GenerationShard;
    cryptographic signature/protocol verification belongs to an upper layer.
    All writes use the caller-owned batch.
    """

    def __init__(
        self,
        shard_placement_repository: ShardPlacementRepository,
        generation_shard_repository: GenerationShardRepository,
        storage_receipt_repository: StorageReceiptRepository,
    ) -> None:
        for name, repository in (
            ("shard_placement_repository", shard_placement_repository),
            ("generation_shard_repository", generation_shard_repository),
            ("storage_receipt_repository", storage_receipt_repository),
        ):
            if repository is None:
                raise TypeError(f"{name} is required")
        self._placements = shard_placement_repository
        self._shards = generation_shard_repository
        self._receipts = storage_receipt_repository

    def register(
        self, receipt: StorageReceipt, *, batch: MetadataBatch
    ) -> StorageReceipt:
        if not isinstance(receipt, StorageReceipt):
            raise TypeError("receipt must be a StorageReceipt")
        if batch is None:
            raise TypeError("batch is required")
        placement = self._placements.get(receipt.placement_id)
        if placement is None:
            raise StorageReceiptPlacementNotFoundError(receipt.placement_id)
        shard = self._shards.get(placement.generation_id, placement.shard_index)
        if shard is None:
            raise StorageReceiptIntegrityError(
                "Placement references a missing GenerationShard"
            )
        self._validate_against(receipt, placement, shard)
        if receipt.issued_at < placement.created_at:
            raise StorageReceiptIntegrityError(
                "Receipt issued_at predates Placement creation"
            )
        if receipt.receipt_type is ReceiptType.STORAGE_VERIFICATION:
            if (
                placement.stored_at is not None
                and receipt.issued_at < placement.stored_at
            ):
                raise StorageReceiptIntegrityError(
                    "Verification receipt predates Placement stored_at"
                )
        if self._receipts.get(receipt.receipt_id) is not None:
            raise StorageReceiptAlreadyExistsError(receipt.receipt_id)
        self._receipts.put(receipt, batch)
        return receipt

    def get(self, receipt_id: str) -> StorageReceipt | None:
        return self._receipts.get(receipt_id)

    def require(self, receipt_id: str) -> StorageReceipt:
        receipt = self.get(receipt_id)
        if receipt is None:
            raise StorageReceiptNotFoundError(receipt_id)
        return receipt

    def list_for_placement(self, placement_id: str) -> tuple[StorageReceipt, ...]:
        placement = self._placements.get(placement_id)
        if placement is None:
            raise StorageReceiptPlacementNotFoundError(placement_id)
        receipts = self._receipts.list_for_placement(placement_id)
        shard = self._shards.get(placement.generation_id, placement.shard_index)
        if shard is None:
            raise StorageReceiptIntegrityError(
                "Placement references a missing GenerationShard"
            )
        for receipt in receipts:
            self._validate_against(receipt, placement, shard)
            self._validate_timestamps(receipt, placement)
        return receipts

    def validate(self, receipt_id: str) -> bool:
        receipt = self.require(receipt_id)
        placement = self._placements.get(receipt.placement_id)
        if placement is None:
            raise StorageReceiptPlacementNotFoundError(receipt.placement_id)
        shard = self._shards.get(placement.generation_id, placement.shard_index)
        if shard is None:
            raise StorageReceiptIntegrityError(
                "Placement references a missing GenerationShard"
            )
        self._validate_against(receipt, placement, shard)
        self._validate_timestamps(receipt, placement)
        return True

    def validate_for_placement(self, placement_id: str) -> bool:
        self.list_for_placement(placement_id)
        return True

    @staticmethod
    def _validate_against(receipt, placement, shard) -> None:
        expected = {
            "placement_id": placement.placement_id,
            "generation_id": placement.generation_id,
            "shard_index": placement.shard_index,
            "storage_node_id": placement.storage_node_id,
            "shard_hash": shard.shard_hash,
            "stored_size": shard.shard_size,
        }
        if any(getattr(receipt, field) != value for field, value in expected.items()):
            raise StorageReceiptIntegrityError(
                "StorageReceipt metadata does not match Placement and GenerationShard"
            )

    @staticmethod
    def _validate_timestamps(receipt, placement) -> None:
        if receipt.issued_at < placement.created_at:
            raise StorageReceiptIntegrityError(
                "Receipt issued_at predates Placement creation"
            )
        if (
            receipt.receipt_type is ReceiptType.STORAGE_VERIFICATION
            and placement.stored_at is not None
            and receipt.issued_at < placement.stored_at
        ):
            raise StorageReceiptIntegrityError(
                "Verification receipt predates Placement stored_at"
            )
