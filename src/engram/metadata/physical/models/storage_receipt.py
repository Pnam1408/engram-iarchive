from dataclasses import dataclass
from enum import StrEnum

from ._validation import (
    validate_hash,
    validate_non_empty_string,
    validate_non_negative_integer,
    validate_positive_integer,
    validate_timestamp,
    validate_uuid,
)


class ReceiptType(StrEnum):
    """Kind of storage evidence returned for a shard placement."""

    STORAGE_ACK = "storage_ack"
    STORAGE_VERIFICATION = "storage_verification"


@dataclass(frozen=True, slots=True)
class StorageReceipt:
    """Immutable, opaque storage evidence for one ShardPlacement.

    The receipt bytes are retained verbatim. Parsing, signature verification,
    issuer authentication, and cross-entity consistency checks belong to
    higher-level services because the concrete receipt protocol is not fixed
    by the domain model.
    """

    receipt_id: str
    placement_id: str

    generation_id: str
    shard_index: int

    storage_node_id: str

    receipt_type: ReceiptType

    shard_hash: bytes
    stored_size: int

    issued_at: int
    receipt_data: bytes

    def __post_init__(self) -> None:
        validate_uuid(self.receipt_id, "receipt_id")
        validate_uuid(self.placement_id, "placement_id")
        validate_uuid(self.generation_id, "generation_id")
        validate_non_negative_integer(self.shard_index, "shard_index")
        validate_non_empty_string(self.storage_node_id, "storage_node_id")

        if not isinstance(self.receipt_type, ReceiptType):
            raise TypeError("receipt_type must be a ReceiptType")

        validate_hash(self.shard_hash, "shard_hash")
        validate_positive_integer(self.stored_size, "stored_size")
        validate_timestamp(self.issued_at, "issued_at")

        if not isinstance(self.receipt_data, bytes):
            raise TypeError("receipt_data must be bytes")
        if not self.receipt_data:
            raise ValueError("receipt_data must not be empty")
