"""Storage backend protocol."""

from typing import Protocol

from engram.processing.storage.models import StorageObject


class StorageBackend(Protocol):
    """Interface implemented by concrete storage backends.

    Implementations must provide idempotent put behavior:

    - Same key and same content: return the existing object.
    - Same key and different content: raise StorageConflictError.

    Implementations must never silently overwrite existing objects.
    """

    def put(self, key: str, data: bytes) -> StorageObject:
        """Store data under key and return its metadata."""
        ...

    def get(self, key: str) -> bytes:
        """Read an object.

        Raises:
            StorageNotFoundError: If the key does not exist.
            StorageIOError: If the underlying read operation fails.
        """
        ...

    def exists(self, key: str) -> bool:
        """Return whether an object exists."""
        ...

    def stat(self, key: str) -> StorageObject:
        """Return metadata for an existing object.

        Raises:
            StorageNotFoundError: If the key does not exist.
        """
        ...

    def delete(self, key: str) -> None:
        """Delete an existing object.

        Raises:
            StorageNotFoundError: If the key does not exist.
        """
        ...
