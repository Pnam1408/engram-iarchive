"""Storage processor for persisting erasure-coded shard sets."""

from __future__ import annotations

from engram.processing.artifacts import EncodedShardSet
from engram.processing.exceptions import (
    InvalidArtifactError,
    StorageIntegrityError,
)
from engram.processing.storage.base import StorageBackend
from engram.processing.storage.models import (
    StorageObject,
    StoredShardReference,
    StoredShardSet,
)


class StorageProcessor:
    """Persist an encoded shard set through a storage backend."""

    def __init__(self, backend: StorageBackend) -> None:
        if backend is None:
            raise InvalidArtifactError("backend must not be None")

        self._backend = backend

    @staticmethod
    def _validate_generation_id(generation_id: str) -> None:
        if not isinstance(generation_id, str) or not generation_id:
            raise InvalidArtifactError("generation_id must be a non-empty string")

        if (
            generation_id in {".", ".."}
            or "/" in generation_id
            or "\\" in generation_id
            or "\x00" in generation_id
        ):
            raise InvalidArtifactError("generation_id must be a single path component")

    @staticmethod
    def _build_storage_key(
        generation_id: str,
        shard_index: int,
    ) -> str:
        return f"generations/{generation_id}" f"/shards/{shard_index}"

    @staticmethod
    def _validate_stored_object(
        stored_object: StorageObject,
        *,
        expected_key: str,
        expected_size: int,
        expected_sha256: str,
    ) -> None:
        if not isinstance(stored_object, StorageObject):
            raise StorageIntegrityError("Backend returned an invalid storage object")

        if (
            stored_object.key != expected_key
            or stored_object.size != expected_size
            or stored_object.sha256 != expected_sha256
        ):
            raise StorageIntegrityError(
                f"Stored object metadata does not match "
                f"the expected shard at key: {expected_key}"
            )

    def store(
        self,
        generation_id: str,
        shard_set: EncodedShardSet,
    ) -> StoredShardSet:
        """Store all shards and return references when complete.

        Successfully stored shards are retained if a later storage
        operation fails. Calling this method again with the same
        generation and shard set is safe when the backend implements
        the StorageBackend immutability contract.
        """
        self._validate_generation_id(generation_id)

        if not isinstance(shard_set, EncodedShardSet):
            raise InvalidArtifactError("shard_set must be an EncodedShardSet")

        references: list[StoredShardReference] = []

        for shard in shard_set.shards:
            storage_key = self._build_storage_key(
                generation_id,
                shard.index,
            )

            stored_object = self._backend.put(
                storage_key,
                shard.data,
            )

            self._validate_stored_object(
                stored_object,
                expected_key=storage_key,
                expected_size=len(shard.data),
                expected_sha256=shard.sha256,
            )

            references.append(
                StoredShardReference(
                    shard_index=shard.index,
                    storage_key=storage_key,
                    size=stored_object.size,
                    sha256=stored_object.sha256,
                )
            )

        return StoredShardSet(
            generation_id=generation_id,
            original_size=shard_set.original_size,
            data_shards=shard_set.data_shards,
            total_shards=shard_set.total_shards,
            shards=tuple(references),
        )
