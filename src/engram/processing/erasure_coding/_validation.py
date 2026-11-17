from engram.processing.contracts import ErasureCodingConfig
from engram.processing.exceptions import (
    ErasureCodingError,
    InvalidConfigurationError,
)


def validate_config(config: ErasureCodingConfig) -> None:
    if not isinstance(config, ErasureCodingConfig):
        raise InvalidConfigurationError("config must be an ErasureCodingConfig")
    if not 1 <= config.data_shards < config.total_shards <= 255:
        raise InvalidConfigurationError(
            "require 1 <= data_shards < total_shards <= 255"
        )


def validate_original_size(original_size: int) -> None:
    if (
        not isinstance(original_size, int)
        or isinstance(original_size, bool)
        or original_size < 0
    ):
        raise ErasureCodingError("original_size must be a non-negative integer")
