"""Storage processing stage."""

from engram.processing.storage.base import StorageBackend
from engram.processing.storage.backends import FilesystemStorageBackend
from engram.processing.storage.config import FilesystemStorageConfig
from engram.processing.storage.processor import StorageProcessor
from engram.processing.storage.models import (
    StorageObject,
    StoredShardReference,
    StoredShardSet,
)

__all__ = [
    "StorageBackend",
    "FilesystemStorageBackend",
    "FilesystemStorageConfig",
    "StorageProcessor",
    "StorageObject",
    "StoredShardReference",
    "StoredShardSet",
]
