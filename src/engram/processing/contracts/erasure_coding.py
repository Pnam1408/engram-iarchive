"""Erasure coding stage protocols and configuration."""

from dataclasses import dataclass
from typing import Protocol

from engram.processing.artifacts import EncodedShard, EncodedShardSet, GenerationPayload
from engram.processing.exceptions import InvalidConfigurationError


@dataclass(frozen=True, slots=True)
class ErasureCodingConfig:
    data_shards: int = 4
    total_shards: int = 6

    def __post_init__(self) -> None:
        if (
            not isinstance(self.data_shards, int)
            or isinstance(self.data_shards, bool)
            or not isinstance(self.total_shards, int)
            or isinstance(self.total_shards, bool)
        ):
            raise InvalidConfigurationError("shard counts must be integers")
        if not 1 <= self.data_shards < self.total_shards <= 255:
            raise InvalidConfigurationError(
                "require 1 <= data_shards < total_shards <= 255"
            )


class ErasureEncoder(Protocol):
    def encode(
        self, payload: GenerationPayload, *, config: ErasureCodingConfig
    ) -> EncodedShardSet: ...


class ErasureDecoder(Protocol):
    def decode(
        self,
        shards: tuple[EncodedShard, ...],
        *,
        original_size: int,
        config: ErasureCodingConfig,
    ) -> bytes: ...
