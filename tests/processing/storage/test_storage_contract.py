"""Tests for the storage backend protocol."""

from engram.processing.storage.base import StorageBackend
from engram.processing.storage.models import StorageObject

EXPECTED_METHODS = (
    "put",
    "get",
    "exists",
    "stat",
    "delete",
)


class DummyStorageBackend:
    """Minimal structural implementation for contract tests."""

    def put(self, key: str, data: bytes) -> StorageObject:
        return StorageObject(
            key=key,
            size=len(data),
            sha256="0" * 64,
        )

    def get(self, key: str) -> bytes:
        return b"data"

    def exists(self, key: str) -> bool:
        return True

    def stat(self, key: str) -> StorageObject:
        return StorageObject(
            key=key,
            size=4,
            sha256="0" * 64,
        )

    def delete(self, key: str) -> None:
        return None


class TestStorageBackendProtocol:
    def test_protocol_declares_all_required_methods(self):
        for method_name in EXPECTED_METHODS:
            assert hasattr(StorageBackend, method_name)
            assert callable(getattr(StorageBackend, method_name))

    def test_dummy_backend_implements_protocol_surface(self):
        backend = DummyStorageBackend()

        for method_name in EXPECTED_METHODS:
            assert callable(getattr(backend, method_name))

    def test_put_returns_storage_object(self):
        backend = DummyStorageBackend()

        result = backend.put("objects/test.bin", b"data")

        assert isinstance(result, StorageObject)
        assert result.key == "objects/test.bin"
        assert result.size == 4

    def test_get_returns_bytes(self):
        backend = DummyStorageBackend()

        assert backend.get("objects/test.bin") == b"data"

    def test_exists_returns_bool(self):
        backend = DummyStorageBackend()

        assert backend.exists("objects/test.bin") is True

    def test_stat_returns_storage_object(self):
        backend = DummyStorageBackend()

        result = backend.stat("objects/test.bin")

        assert isinstance(result, StorageObject)

    def test_delete_returns_none(self):
        backend = DummyStorageBackend()

        assert backend.delete("objects/test.bin") is None
