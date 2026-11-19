class MetadataError(Exception):
    """Base exception for metadata layer."""


class UnknownColumnFamilyError(MetadataError):
    """Raised when a column family does not exist."""


class MetadataClosedError(MetadataError):
    """Raised when accessing a closed database."""


class KeyCodecError(MetadataError):
    """Raised when a metadata key cannot be decoded."""


class SerializationError(MetadataError):
    """Raised when metadata serialization or deserialization fails."""
