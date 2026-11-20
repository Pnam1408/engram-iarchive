from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ShardAvailabilitySnapshot:
    """Verified shard indices for one Generation; duplicate placements count once."""

    generation_id: str
    verified_shard_indices: frozenset[int]

    def __post_init__(self) -> None:
        if not isinstance(self.generation_id, str) or not self.generation_id.strip():
            raise ValueError("generation_id must be a non-empty string")
        if not isinstance(self.verified_shard_indices, frozenset):
            raise TypeError("verified_shard_indices must be a frozenset")
        if any(
            isinstance(i, bool) or not isinstance(i, int) or i < 0
            for i in self.verified_shard_indices
        ):
            raise ValueError("verified shard indices must be non-negative integers")
