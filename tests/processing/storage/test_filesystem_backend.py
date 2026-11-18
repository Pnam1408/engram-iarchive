import hashlib

import pytest

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

from engram.processing.exceptions import (
    InvalidStorageKeyError,
    StorageConflictError,
    StorageIOError,
    StorageNotFoundError,
)
from engram.processing.storage.backends import FilesystemStorageBackend
from engram.processing.storage.config import FilesystemStorageConfig
import engram.processing.storage.backends.filesystem as filesystem_module


@pytest.fixture
def backend(tmp_path):
    return FilesystemStorageBackend(
        FilesystemStorageConfig(root_dir=tmp_path / "storage")
    )


def test_put_and_get(backend):
    data = b"hello engram"

    stored = backend.put(
        "generations/gen-001/shards/0",
        data,
    )

    assert stored.size == len(data)
    assert stored.sha256 == hashlib.sha256(data).hexdigest()

    assert backend.get(stored.key) == data


def test_put_same_content_is_idempotent(backend):
    key = "generations/gen-001/shards/0"
    data = b"same content"

    first = backend.put(key, data)
    second = backend.put(key, data)

    assert first == second
    assert backend.get(key) == data


def test_put_different_content_raises_conflict(backend):
    key = "generations/gen-001/shards/0"

    backend.put(key, b"first")

    with pytest.raises(StorageConflictError):
        backend.put(key, b"second")

    assert backend.get(key) == b"first"


def test_exists_and_stat(backend):
    key = "generations/gen-001/shards/0"
    data = b"payload"

    assert not backend.exists(key)

    backend.put(key, data)

    assert backend.exists(key)

    metadata = backend.stat(key)

    assert metadata.size == len(data)
    assert metadata.sha256 == hashlib.sha256(data).hexdigest()


def test_get_missing_object_raises(backend):
    with pytest.raises(StorageNotFoundError):
        backend.get("generations/missing/shards/0")


def test_delete_missing_object_raises(backend):
    with pytest.raises(StorageNotFoundError):
        backend.delete("generations/missing/shards/0")


@pytest.mark.parametrize(
    "key",
    [
        "",
        "/absolute/path",
        "../outside",
        "generations/../outside",
        "generations/./shards/0",
        r"generations\gen-001\shards\0",
    ],
)
def test_invalid_storage_key(backend, key):
    with pytest.raises(InvalidStorageKeyError):
        backend.put(key, b"payload")


def create_symlink_or_skip(
    link: Path,
    target: Path,
    *,
    target_is_directory: bool = False,
) -> None:
    try:
        link.symlink_to(
            target,
            target_is_directory=target_is_directory,
        )
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Symlink is not supported: {exc}")


def test_put_rejects_symlink_directory(tmp_path):
    root = tmp_path / "storage"
    outside = tmp_path / "outside"
    outside.mkdir()

    root.mkdir()
    create_symlink_or_skip(
        root / "generations",
        outside,
        target_is_directory=True,
    )

    backend = FilesystemStorageBackend(FilesystemStorageConfig(root_dir=root))

    with pytest.raises(StorageIOError):
        backend.put("generations/gen-001/shards/0", b"payload")

    assert not (outside / "gen-001").exists()


def test_get_rejects_symlink_object(backend, tmp_path):
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside data")

    link = tmp_path / "storage" / "generations" / "gen-001" / "shards" / "0"
    link.parent.mkdir(parents=True)
    create_symlink_or_skip(link, outside)

    with pytest.raises(StorageIOError):
        backend.get("generations/gen-001/shards/0")


def test_put_rejects_symlink_object(backend, tmp_path):
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"original")

    link = tmp_path / "storage" / "generations" / "gen-001" / "shards" / "0"
    link.parent.mkdir(parents=True)
    create_symlink_or_skip(link, outside)

    with pytest.raises(StorageIOError):
        backend.put("generations/gen-001/shards/0", b"replacement")

    assert outside.read_bytes() == b"original"


@pytest.mark.parametrize("operation", ["exists", "stat", "delete"])
def test_operations_reject_symlink_object(
    backend,
    tmp_path,
    operation,
):
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside data")

    link = tmp_path / "storage" / "generations" / "gen-001" / "shards" / "0"
    link.parent.mkdir(parents=True)
    create_symlink_or_skip(link, outside)

    key = "generations/gen-001/shards/0"

    with pytest.raises(StorageIOError):
        getattr(backend, operation)(key)

    assert outside.read_bytes() == b"outside data"


def test_put_cleans_temp_file_when_commit_fails(
    backend,
    monkeypatch,
    tmp_path,
):
    def fail_link(*args, **kwargs):
        raise OSError("Simulated filesystem failure")

    monkeypatch.setattr(filesystem_module.os, "link", fail_link)

    key = "generations/gen-001/shards/0"

    with pytest.raises(StorageIOError):
        backend.put(key, b"payload")

    storage_root = tmp_path / "storage"

    assert not (storage_root / key).exists()

    temp_files = list(storage_root.rglob(".tmp-*"))
    assert temp_files == []


def test_put_cleans_temp_file_after_success(backend, tmp_path):
    key = "generations/gen-001/shards/0"

    backend.put(key, b"payload")

    temp_files = list((tmp_path / "storage").rglob(".tmp-*"))

    assert temp_files == []


def test_put_empty_bytes_is_supported(backend):
    key = "generations/gen-001/shards/0"

    result = backend.put(key, b"")

    assert result.size == 0
    assert backend.get(key) == b""


def test_concurrent_put_same_content_is_idempotent(
    tmp_path,
):
    root = tmp_path / "storage"
    key = "generations/gen-001/shards/0"
    data = b"concurrent payload"

    worker_count = 8
    barrier = Barrier(worker_count)

    def write():
        backend = FilesystemStorageBackend(FilesystemStorageConfig(root_dir=root))

        barrier.wait()

        return backend.put(key, data)

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        results = list(executor.map(lambda _: write(), range(worker_count)))

    assert len(results) == worker_count
    assert all(result == results[0] for result in results)

    backend = FilesystemStorageBackend(FilesystemStorageConfig(root_dir=root))

    assert backend.get(key) == data
    assert backend.stat(key) == results[0]


def test_concurrent_put_different_content_conflicts(
    tmp_path,
):
    root = tmp_path / "storage"
    key = "generations/gen-001/shards/0"

    payloads = [f"payload-{index}".encode() for index in range(8)]

    worker_count = len(payloads)
    barrier = Barrier(worker_count)

    def write(data: bytes):
        backend = FilesystemStorageBackend(FilesystemStorageConfig(root_dir=root))

        barrier.wait()

        try:
            result = backend.put(key, data)
            return ("success", data, result)
        except StorageConflictError:
            return ("conflict", data, None)

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        results = list(executor.map(write, payloads))

    successes = [result for result in results if result[0] == "success"]

    conflicts = [result for result in results if result[0] == "conflict"]

    assert len(successes) == 1
    assert len(conflicts) == worker_count - 1

    backend = FilesystemStorageBackend(FilesystemStorageConfig(root_dir=root))

    stored_data = backend.get(key)

    assert stored_data == successes[0][1]
