"""Configuration for content-defined chunking."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FastCDCConfig:
    """FastCDC v2020 parameters, expressed in bytes.

    The defaults target small research workloads while retaining the usual
    min/average/max relationship. The average size must be a power of two,
    as expected by the FastCDC normalization masks.
    """

    min_size: int = 2 * 1024
    avg_size: int = 4 * 1024
    max_size: int = 8 * 1024

    def __post_init__(self) -> None:
        if self.min_size < 64:
            raise ValueError("min_size must be at least 64 bytes")
        if not (self.min_size <= self.avg_size <= self.max_size):
            raise ValueError("chunk sizes must satisfy min_size <= avg_size <= max_size")
        if self.avg_size & (self.avg_size - 1):
            raise ValueError("avg_size must be a power of two")
        if self.max_size > 0xFFFF_FFFF:
            raise ValueError("max_size must fit in an unsigned 32-bit integer")
