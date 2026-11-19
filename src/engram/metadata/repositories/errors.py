class RepositoryError(Exception):
    """Base exception for metadata repository failures."""


class RecordAlreadyExistsError(RepositoryError):
    """Raised when attempting to overwrite an immutable record."""


class CorruptMetadataError(RepositoryError):
    """Raised when persisted metadata is malformed or inconsistent with its key."""
