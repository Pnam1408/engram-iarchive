"""Configuration types for processing chunkers."""

from __future__ import annotations

from dataclasses import dataclass

from engram.processing.exceptions import InvalidConfigurationError


@dataclass(frozen=True, slots=True)
class FastCDCConfig:
    """FastCDC v2020 chunk-size parameters, expressed in bytes.

    ``avg_size`` must be a power of two because FastCDC uses normalization
    masks derived from the average chunk size.
    """

    min_size: int = 2 * 1024
    avg_size: int = 4 * 1024
    max_size: int = 8 * 1024

    def __post_init__(self) -> None:
        values = (self.min_size, self.avg_size, self.max_size)
        if any(not isinstance(value, int) or isinstance(value, bool) for value in values):
            raise InvalidConfigurationError("FastCDC sizes must be integers")
        if self.min_size < 64:
            raise InvalidConfigurationError("min_size must be at least 64 bytes")
        if not self.min_size <= self.avg_size <= self.max_size:
            raise InvalidConfigurationError(
                "chunk sizes must satisfy min_size <= avg_size <= max_size"
            )
        if self.avg_size & (self.avg_size - 1):
            raise InvalidConfigurationError("avg_size must be a power of two")
        if self.max_size > 0xFFFF_FFFF:
            raise InvalidConfigurationError(
                "max_size must fit in an unsigned 32-bit integer"
            )
