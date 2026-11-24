"""Persistable immutable internal-node record for an object's Version History MMR."""

from dataclasses import dataclass
from uuid import UUID


def _validate_object_id(value: str) -> None:
    if not isinstance(value, str):
        raise TypeError("object_id must be a string")
    try:
        parsed = UUID(value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("object_id must be a valid UUID") from exc
    if parsed.version != 4:
        raise ValueError("object_id must be a UUIDv4")


@dataclass(frozen=True, slots=True)
class MMRNodeRecord:
    object_id: str
    start_leaf: int
    height: int
    hash: bytes

    def __post_init__(self) -> None:
        _validate_object_id(self.object_id)
        if isinstance(self.start_leaf, bool) or not isinstance(self.start_leaf, int):
            raise TypeError("start_leaf must be an integer")
        if not 0 <= self.start_leaf < (1 << 64):
            raise ValueError("start_leaf must fit uint64")
        if isinstance(self.height, bool) or not isinstance(self.height, int):
            raise TypeError("height must be an integer")
        if not 1 <= self.height <= 63:
            raise ValueError("height must be in [1, 63]")
        if self.start_leaf % (1 << self.height):
            raise ValueError("start_leaf must be aligned to subtree size")
        if not isinstance(self.hash, bytes):
            raise TypeError("hash must be bytes")
        if len(self.hash) != 32:
            raise ValueError("hash must be exactly 32 bytes")
