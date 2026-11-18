"""Tests for storage data models."""

from dataclasses import FrozenInstanceError
import hashlib

import pytest

from engram.processing.exceptions import InvalidArtifactError
from engram.processing.storage.models import (
    StorageObject,
    StoredShardReference,
    StoredShardSet,
)


def make_sha256(data: bytes = b"test-data") -> str:
    return hashlib.sha256(data).hexdigest()


def make_storage_object(
    *,
    key: str = "objects/test.bin",
    size: int = 100,
    sha256: str | None = None,
) -> StorageObject:
    return StorageObject(
        key=key,
        size=size,
        sha256=sha256 or make_sha256(),
    )


def make_reference(
    index: int,
    *,
    storage_key: str | None = None,
    size: int = 100,
    sha256: str | None = None,
) -> StoredShardReference:
    return StoredShardReference(
        shard_index=index,
        storage_key=(
            f"generations/gen-001/shards/{index}"
            if storage_key is None
            else storage_key
        ),
        size=size,
        sha256=sha256 or make_sha256(f"shard-{index}".encode()),
    )


def make_shard_set(
    *,
    generation_id: str = "gen-001",
    original_size: int = 1000,
    data_shards: int = 2,
    total_shards: int = 4,
    shards: tuple[StoredShardReference, ...] | None = None,
) -> StoredShardSet:
    if shards is None:
        shards = tuple(make_reference(index) for index in range(total_shards))

    return StoredShardSet(
        generation_id=generation_id,
        original_size=original_size,
        data_shards=data_shards,
        total_shards=total_shards,
        shards=shards,
    )


class TestStorageObject:
    def test_create_valid_storage_object(self):
        obj = make_storage_object()

        assert obj.key == "objects/test.bin"
        assert obj.size == 100
        assert obj.sha256 == make_sha256()

    def test_zero_size_is_allowed(self):
        obj = make_storage_object(size=0)

        assert obj.size == 0

    @pytest.mark.parametrize("key", ["", None, 123])
    def test_reject_invalid_key(self, key):
        with pytest.raises(InvalidArtifactError):
            StorageObject(
                key=key,
                size=10,
                sha256=make_sha256(),
            )

    @pytest.mark.parametrize("size", [-1, 1.5, "10", True])
    def test_reject_invalid_size(self, size):
        with pytest.raises(InvalidArtifactError):
            StorageObject(
                key="objects/test.bin",
                size=size,
                sha256=make_sha256(),
            )

    @pytest.mark.parametrize(
        "digest",
        [
            "",
            "abc",
            "g" * 64,
            "A" * 64,
            None,
            123,
        ],
    )
    def test_reject_invalid_sha256(self, digest):
        with pytest.raises(InvalidArtifactError):
            StorageObject(
                key="objects/test.bin",
                size=10,
                sha256=digest,
            )

    def test_storage_object_is_immutable(self):
        obj = make_storage_object()

        with pytest.raises(FrozenInstanceError):
            obj.size = 200


class TestStoredShardReference:
    def test_create_valid_reference(self):
        reference = make_reference(0)

        assert reference.shard_index == 0
        assert reference.storage_key.endswith("/0")
        assert reference.size == 100
        assert len(reference.sha256) == 64

    @pytest.mark.parametrize("index", [-1, 1.5, "0", True])
    def test_reject_invalid_shard_index(self, index):
        with pytest.raises(InvalidArtifactError):
            make_reference(index)

    @pytest.mark.parametrize(
        "storage_key",
        ["", 123],
    )
    def test_reject_invalid_storage_key(self, storage_key):
        with pytest.raises(InvalidArtifactError):
            make_reference(
                0,
                storage_key=storage_key,
            )

    @pytest.mark.parametrize("size", [0, -1, 1.5, "10", True])
    def test_reject_invalid_size(self, size):
        with pytest.raises(InvalidArtifactError):
            make_reference(0, size=size)

    def test_reject_invalid_sha256(self):
        with pytest.raises(InvalidArtifactError):
            make_reference(0, sha256="invalid")

    def test_reference_is_immutable(self):
        reference = make_reference(0)

        with pytest.raises(FrozenInstanceError):
            reference.size = 200


class TestStoredShardSet:
    def test_create_valid_shard_set(self):
        shard_set = make_shard_set()

        assert shard_set.generation_id == "gen-001"
        assert shard_set.original_size == 1000
        assert shard_set.data_shards == 2
        assert shard_set.total_shards == 4
        assert len(shard_set.shards) == 4

    def test_preserve_shard_order(self):
        shard_set = make_shard_set()

        assert tuple(shard.shard_index for shard in shard_set.shards) == (0, 1, 2, 3)

    def test_reject_empty_generation_id(self):
        with pytest.raises(InvalidArtifactError):
            make_shard_set(generation_id="")

    @pytest.mark.parametrize(
        "original_size",
        [-1, 1.5, "100", True],
    )
    def test_reject_invalid_original_size(self, original_size):
        with pytest.raises(InvalidArtifactError):
            make_shard_set(original_size=original_size)

    @pytest.mark.parametrize(
        ("data_shards", "total_shards"),
        [
            (0, 4),
            (-1, 4),
            (2, 2),
            (4, 2),
            (4, 256),
            (True, 4),
            (2, True),
            (2.5, 4),
        ],
    )
    def test_reject_invalid_erasure_coding_config(
        self,
        data_shards,
        total_shards,
    ):
        with pytest.raises(InvalidArtifactError):
            make_shard_set(
                data_shards=data_shards,
                total_shards=total_shards,
            )

    def test_reject_non_tuple_shards(self):
        with pytest.raises(InvalidArtifactError):
            make_shard_set(shards=[])

    def test_reject_wrong_number_of_shards(self):
        shards = (
            make_reference(0),
            make_reference(1),
        )

        with pytest.raises(InvalidArtifactError):
            make_shard_set(shards=shards)

    def test_reject_invalid_shard_type(self):
        shards = (
            make_reference(0),
            make_reference(1),
            make_reference(2),
            object(),
        )

        with pytest.raises(InvalidArtifactError):
            make_shard_set(shards=shards)

    @pytest.mark.parametrize(
        "indexes",
        [
            (0, 1, 2, 4),
            (0, 1, 1, 3),
            (1, 0, 2, 3),
        ],
    )
    def test_reject_invalid_shard_indexes(self, indexes):
        shards = tuple(make_reference(index) for index in indexes)

        with pytest.raises(InvalidArtifactError):
            make_shard_set(shards=shards)

    def test_reject_duplicate_storage_keys(self):
        shards = (
            make_reference(0, storage_key="same-key"),
            make_reference(1, storage_key="same-key"),
            make_reference(2),
            make_reference(3),
        )

        with pytest.raises(InvalidArtifactError):
            make_shard_set(shards=shards)

    def test_shard_set_is_immutable(self):
        shard_set = make_shard_set()

        with pytest.raises(FrozenInstanceError):
            shard_set.original_size = 2000

    def test_shard_tuple_is_immutable(self):
        shard_set = make_shard_set()

        with pytest.raises(TypeError):
            shard_set.shards[0] = make_reference(0)
