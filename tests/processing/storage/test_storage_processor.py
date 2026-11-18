from __future__ import annotations

import hashlib

import pytest

from engram.processing.artifacts import (
    EncodedShard,
    EncodedShardSet,
)
from engram.processing.exceptions import (
    InvalidArtifactError,
    StorageConflictError,
    StorageIOError,
    StorageIntegrityError,
    StorageNotFoundError,
)
from engram.processing.storage.models import StorageObject
from engram.processing.storage.processor import StorageProcessor


class FakeStorageBackend:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.put_calls: list[str] = []
        self.fail_key: str | None = None
        self.fail_once = False
        self.invalid_metadata = False

    def put(self, key: str, data: bytes) -> StorageObject:
        self.put_calls.append(key)

        if self.fail_once and key == self.fail_key:
            self.fail_once = False
            raise StorageIOError("Simulated storage failure")

        existing = self.objects.get(key)

        if existing is not None:
            if existing != data:
                raise StorageConflictError(key)
        else:
            self.objects[key] = data

        result = StorageObject(
            key=key,
            size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        )

        if self.invalid_metadata:
            return StorageObject(
                key=key,
                size=result.size + 1,
                sha256=result.sha256,
            )

        return result

    def get(self, key: str) -> bytes:
        try:
            return self.objects[key]
        except KeyError as exc:
            raise StorageNotFoundError(key) from exc

    def exists(self, key: str) -> bool:
        return key in self.objects

    def stat(self, key: str) -> StorageObject:
        data = self.get(key)
        return StorageObject(
            key=key,
            size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        )

    def delete(self, key: str) -> None:
        try:
            del self.objects[key]
        except KeyError as exc:
            raise StorageNotFoundError(key) from exc


@pytest.fixture
def backend() -> FakeStorageBackend:
    return FakeStorageBackend()


@pytest.fixture
def processor(backend: FakeStorageBackend) -> StorageProcessor:
    return StorageProcessor(backend)


@pytest.fixture
def shard_set() -> EncodedShardSet:
    data = (
        b"shard-0",
        b"shard-1",
        b"shard-2",
        b"shard-3",
    )

    shards = tuple(
        EncodedShard(
            index=index,
            data=payload,
            sha256=hashlib.sha256(payload).hexdigest(),
        )
        for index, payload in enumerate(data)
    )

    return EncodedShardSet(
        shards=shards,
        original_size=14,
        data_shards=2,
        total_shards=4,
    )


def test_store_returns_complete_stored_shard_set(
    processor,
    backend,
    shard_set,
):
    result = processor.store("gen-001", shard_set)

    assert result.generation_id == "gen-001"
    assert result.original_size == shard_set.original_size
    assert result.data_shards == shard_set.data_shards
    assert result.total_shards == shard_set.total_shards
    assert len(result.shards) == shard_set.total_shards

    for reference, shard in zip(
        result.shards,
        shard_set.shards,
        strict=True,
    ):
        expected_key = f"generations/gen-001/shards/{shard.index}"

        assert reference.shard_index == shard.index
        assert reference.storage_key == expected_key
        assert reference.size == len(shard.data)
        assert reference.sha256 == shard.sha256
        assert backend.get(expected_key) == shard.data


def test_store_can_retry_after_partial_failure(
    processor,
    backend,
    shard_set,
):
    backend.fail_key = "generations/gen-001/shards/2"
    backend.fail_once = True

    with pytest.raises(StorageIOError):
        processor.store("gen-001", shard_set)

    # Hai shard đầu đã được lưu.
    assert backend.exists("generations/gen-001/shards/0")
    assert backend.exists("generations/gen-001/shards/1")

    # Shard gây lỗi và các shard phía sau chưa được lưu.
    assert not backend.exists("generations/gen-001/shards/2")
    assert not backend.exists("generations/gen-001/shards/3")

    result = processor.store("gen-001", shard_set)

    assert len(result.shards) == shard_set.total_shards

    # Các shard đầu được tái sử dụng, không phải ghi lại.
    assert backend.put_calls.count("generations/gen-001/shards/0") == 2

    assert backend.put_calls.count("generations/gen-001/shards/1") == 2

    # Toàn bộ object cuối cùng phải chính xác.
    for reference, shard in zip(
        result.shards,
        shard_set.shards,
        strict=True,
    ):
        assert backend.get(reference.storage_key) == shard.data


def test_store_same_generation_and_data_is_idempotent(
    processor,
    backend,
    shard_set,
):
    first = processor.store("gen-001", shard_set)
    first_call_count = len(backend.put_calls)

    second = processor.store("gen-001", shard_set)

    assert first == second
    assert len(backend.put_calls) == first_call_count + shard_set.total_shards


def test_store_different_data_for_same_generation_raises_conflict(
    processor,
    shard_set,
):
    processor.store("gen-001", shard_set)

    changed_data = b"change0"
    changed_shard = EncodedShard(
        index=0,
        data=changed_data,
        sha256=hashlib.sha256(changed_data).hexdigest(),
    )

    changed_shards = (
        changed_shard,
        *shard_set.shards[1:],
    )

    changed_set = EncodedShardSet(
        shards=changed_shards,
        original_size=shard_set.original_size,
        data_shards=shard_set.data_shards,
        total_shards=shard_set.total_shards,
    )

    with pytest.raises(StorageConflictError):
        processor.store("gen-001", changed_set)


def test_store_rejects_invalid_backend_metadata(
    processor,
    backend,
    shard_set,
):
    backend.invalid_metadata = True

    with pytest.raises(StorageIntegrityError):
        processor.store("gen-001", shard_set)


@pytest.mark.parametrize(
    "generation_id",
    [
        "",
        ".",
        "..",
        "../outside",
        "gen/001",
        r"gen\001",
        "gen\x00001",
    ],
)
def test_store_rejects_invalid_generation_id(
    processor,
    shard_set,
    generation_id,
):
    with pytest.raises(InvalidArtifactError):
        processor.store(generation_id, shard_set)


def test_store_rejects_invalid_shard_set(processor):
    with pytest.raises(InvalidArtifactError):
        processor.store("gen-001", b"not a shard set")


def test_processor_rejects_none_backend():
    with pytest.raises(InvalidArtifactError):
        StorageProcessor(None)
