from ..models import ReceiptType, StorageReceipt
from ..models._validation import validate_uuid
from ...database import ColumnFamily, KeyCodec
from ...database.serialization import decode_bytes, deserialize, encode_bytes
from ...repositories import (
    BaseRepository,
    CorruptMetadataError,
    RecordAlreadyExistsError,
)


class StorageReceiptRepository(BaseRepository):
    """Immutable append-only storage evidence, indexed by shard placement."""

    _FIELDS = {
        "receipt_id",
        "placement_id",
        "generation_id",
        "shard_index",
        "storage_node_id",
        "receipt_type",
        "shard_hash",
        "stored_size",
        "issued_at",
        "receipt_data",
    }

    @staticmethod
    def _to_dict(receipt: StorageReceipt) -> dict:
        return {
            "receipt_id": receipt.receipt_id,
            "placement_id": receipt.placement_id,
            "generation_id": receipt.generation_id,
            "shard_index": receipt.shard_index,
            "storage_node_id": receipt.storage_node_id,
            "receipt_type": receipt.receipt_type.value,
            "shard_hash": encode_bytes(receipt.shard_hash),
            "stored_size": receipt.stored_size,
            "issued_at": receipt.issued_at,
            "receipt_data": encode_bytes(receipt.receipt_data),
        }

    @classmethod
    def _from_dict(cls, data: dict) -> StorageReceipt:
        cls._require_fields(data, cls._FIELDS, "StorageReceipt")
        return StorageReceipt(
            receipt_id=data["receipt_id"],
            placement_id=data["placement_id"],
            generation_id=data["generation_id"],
            shard_index=data["shard_index"],
            storage_node_id=data["storage_node_id"],
            receipt_type=ReceiptType(data["receipt_type"]),
            shard_hash=decode_bytes(data["shard_hash"], "shard_hash"),
            stored_size=data["stored_size"],
            issued_at=data["issued_at"],
            receipt_data=decode_bytes(data["receipt_data"], "receipt_data"),
        )

    def _decode_primary(self, raw: bytes, receipt_id: str) -> StorageReceipt:
        try:
            data = self._require_dict(deserialize(raw), "StorageReceipt")
            receipt = self._from_dict(data)
            if receipt.receipt_id != receipt_id:
                raise CorruptMetadataError(
                    "StorageReceipt ID does not match storage key"
                )
            return receipt
        except CorruptMetadataError:
            raise
        except Exception as exc:
            raise CorruptMetadataError("Cannot decode stored StorageReceipt") from exc

    def get(self, receipt_id: str) -> StorageReceipt | None:
        self._require_non_empty_string(receipt_id, "receipt_id")
        validate_uuid(receipt_id, "receipt_id")
        raw = self.db.get(
            ColumnFamily.STORAGE_RECEIPTS,
            KeyCodec.storage_receipt_key(receipt_id),
        )
        return None if raw is None else self._decode_primary(raw, receipt_id)

    def list_for_placement(self, placement_id: str) -> tuple[StorageReceipt, ...]:
        self._require_non_empty_string(placement_id, "placement_id")
        validate_uuid(placement_id, "placement_id")
        prefix = KeyCodec.storage_receipt_index_prefix(placement_id)
        receipts = []

        for key, raw_index in self.db.iter_prefix(
            ColumnFamily.STORAGE_RECEIPT_INDEX, prefix
        ):
            try:
                if not key.startswith(prefix):
                    raise CorruptMetadataError("StorageReceipt index prefix mismatch")
                issued_at, offset = KeyCodec.decode_uint64(key, len(prefix))
                receipt_id, end = KeyCodec.decode_string(key, offset)
                if end != len(key):
                    raise CorruptMetadataError("Invalid StorageReceipt index key")
                validate_uuid(receipt_id, "receipt_id")

                index_data = self._require_dict(
                    deserialize(raw_index), "StorageReceipt index"
                )
                self._require_fields(
                    index_data,
                    {"placement_id", "issued_at", "receipt_id"},
                    "StorageReceipt index",
                )
                if index_data != {
                    "placement_id": placement_id,
                    "issued_at": issued_at,
                    "receipt_id": receipt_id,
                }:
                    raise CorruptMetadataError(
                        "StorageReceipt index value does not match its key"
                    )

                receipt = self.get(receipt_id)
                if receipt is None:
                    raise CorruptMetadataError(
                        "StorageReceipt index references a missing primary record"
                    )
                if (
                    receipt.placement_id != placement_id
                    or receipt.issued_at != issued_at
                    or KeyCodec.storage_receipt_index_key(
                        placement_id, issued_at, receipt_id
                    )
                    != key
                ):
                    raise CorruptMetadataError(
                        "StorageReceipt index does not match primary record"
                    )
                receipts.append(receipt)
            except CorruptMetadataError:
                raise
            except Exception as exc:
                raise CorruptMetadataError(
                    "Cannot decode StorageReceipt secondary index"
                ) from exc

        # RocksDB's byte ordering already follows (issued_at, receipt_id).
        return tuple(receipts)

    def put(self, receipt: StorageReceipt, batch) -> None:
        if not isinstance(receipt, StorageReceipt):
            raise TypeError("receipt must be a StorageReceipt")
        if batch is None:
            raise TypeError("batch is required")

        primary_key = KeyCodec.storage_receipt_key(receipt.receipt_id)
        index_key = KeyCodec.storage_receipt_index_key(
            receipt.placement_id, receipt.issued_at, receipt.receipt_id
        )
        if self.db.get(ColumnFamily.STORAGE_RECEIPTS, primary_key) is not None:
            raise RecordAlreadyExistsError(
                f"StorageReceipt {receipt.receipt_id!r} already exists"
            )
        if self.db.get(ColumnFamily.STORAGE_RECEIPT_INDEX, index_key) is not None:
            raise CorruptMetadataError(
                "StorageReceipt secondary index exists without its primary record"
            )

        self._put_batch(
            batch,
            ColumnFamily.STORAGE_RECEIPTS,
            primary_key,
            self._to_dict(receipt),
        )
        self._put_batch(
            batch,
            ColumnFamily.STORAGE_RECEIPT_INDEX,
            index_key,
            {
                "placement_id": receipt.placement_id,
                "issued_at": receipt.issued_at,
                "receipt_id": receipt.receipt_id,
            },
        )
