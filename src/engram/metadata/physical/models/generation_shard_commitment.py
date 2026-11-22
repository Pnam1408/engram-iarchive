from dataclasses import dataclass

from ._validation import validate_positive_integer, validate_timestamp, validate_uuid

BN254_SCALAR_MODULUS = (
    21888242871839275222246405745257275088548364400416034343698204186575808495617
)
SHARD_COMMITMENT_PROTOCOL = "ENGRAM-SHARD-MERKLE/1"


def validate_field_element_bytes(value: bytes, field_name: str) -> None:
    if not isinstance(value, bytes):
        raise TypeError(f"{field_name} must be bytes")
    if len(value) != 32:
        raise ValueError(f"{field_name} must be exactly 32 bytes")
    if int.from_bytes(value, "big") >= BN254_SCALAR_MODULUS:
        raise ValueError(f"{field_name} is outside the BN254 scalar field")


@dataclass(frozen=True, slots=True)
class GenerationShardCommitment:
    generation_id: str
    protocol: str
    shard_count: int
    global_root: bytes
    created_at: int

    def __post_init__(self) -> None:
        validate_uuid(self.generation_id, "generation_id")
        if not isinstance(self.protocol, str) or not self.protocol:
            raise ValueError("protocol must be a non-empty string")
        if self.protocol != SHARD_COMMITMENT_PROTOCOL:
            raise ValueError("unsupported shard commitment protocol")
        validate_positive_integer(self.shard_count, "shard_count")
        if self.shard_count > (1 << 64) - 1:
            raise ValueError("shard_count exceeds uint64 range")
        validate_field_element_bytes(self.global_root, "global_root")
        validate_timestamp(self.created_at, "created_at")
