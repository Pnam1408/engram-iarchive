"""Configuration for the AES-256-GCM processing implementation."""

from dataclasses import dataclass

from engram.processing.exceptions import InvalidConfigurationError


@dataclass(frozen=True, slots=True)
class AESGCMConfig:
    aad_version: int = 1

    def __post_init__(self) -> None:
        if (
            isinstance(self.aad_version, bool)
            or not isinstance(self.aad_version, int)
            or self.aad_version <= 0
        ):
            raise InvalidConfigurationError("aad_version must be a positive integer")
        if self.aad_version != 1:
            raise InvalidConfigurationError(
                f"Unsupported AAD version: {self.aad_version}"
            )
