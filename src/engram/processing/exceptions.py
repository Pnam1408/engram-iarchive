"""Common exceptions raised by processing modules."""


class ProcessingError(Exception):
    """Base class for expected processing failures."""


class InvalidArtifactError(ProcessingError, ValueError):
    """An artifact violates its structural or integrity invariants."""


class InvalidConfigurationError(ProcessingError, ValueError):
    """A processing configuration is invalid."""


class ChunkingError(ProcessingError):
    """A chunking operation failed."""


class EncryptionError(ProcessingError):
    """An encryption operation failed."""


class DecryptionError(EncryptionError):
    """Decryption or authenticated-integrity verification failed."""


class PayloadError(ProcessingError):
    """A generation payload could not be built or parsed."""


class UnsupportedPayloadVersionError(PayloadError):
    """The payload format version is not supported by this implementation."""


class ErasureCodingError(ProcessingError):
    """Erasure encoding or reconstruction failed."""


class InsufficientShardsError(ErasureCodingError):
    """Fewer than the required number of distinct shards are available."""


class ShardIntegrityError(ErasureCodingError):
    """A shard failed its integrity check."""
