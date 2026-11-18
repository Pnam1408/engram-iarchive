"""End-to-end integration tests for processing stages P1-P4.

These tests use the real processing implementations and a real filesystem
backend. They intentionally do not involve metadata repositories or the
future orchestration layer.
"""

from __future__ import annotations

import hashlib

import pytest

from engram.processing.artifacts import (
    EncodedShard,
    GenerationPayload,
)
from engram.processing.chunking import FixedSizeChunker
from engram.processing.contracts import ErasureCodingConfig
from engram.processing.encryption import (
    AESGCMDecryptor,
    AESGCMEncryptor,
    generate_key,
)
from engram.processing.erasure_coding import (
    ReedSolomonDecoder,
    ReedSolomonEncoder,
)
from engram.processing.payload import (
    BinaryPayloadBuilder,
    BinaryPayloadParser,
    PAYLOAD_FORMAT_VERSION,
)
from engram.processing.storage import (
    FilesystemStorageBackend,
    FilesystemStorageConfig,
    StorageProcessor,
)

OBJECT_ID = "pipeline-integration-object"
KEY_ID = "pipeline-integration-key"
GENERATION_ID = "pipeline-integration-generation"


def _original_data() -> bytes:
    """Deterministic binary input with null and non-UTF8 bytes."""
    block = bytes(range(256))
    return (block * 13) + b"\x00ENGRAM\xff\xfe\x80pipeline-tail"


def _run_forward_pipeline(original: bytes):
    key = generate_key()

    # P1: chunking.
    chunks = FixedSizeChunker(chunk_size=333).chunk(original)
    assert chunks
    assert b"".join(chunk.data for chunk in chunks) == original

    # P1: client-side AES-256-GCM encryption.
    encryptor = AESGCMEncryptor()
    encrypted_chunks = tuple(
        encryptor.encrypt(
            chunk,
            object_id=OBJECT_ID,
            chunk_id=f"{OBJECT_ID}:chunk:{chunk.index}",
            key=key,
            key_id=KEY_ID,
        )
        for chunk in chunks
    )

    # P2: pack self-describing encrypted records into one generation payload.
    payload = BinaryPayloadBuilder().build(encrypted_chunks)

    # P3: Reed-Solomon encode the serialized payload.
    coding_config = ErasureCodingConfig(data_shards=3, total_shards=5)
    encoded_shards = ReedSolomonEncoder().encode(
        payload,
        config=coding_config,
    )

    return key, chunks, encrypted_chunks, payload, coding_config, encoded_shards


def _restore_from_storage(
    *,
    backend: FilesystemStorageBackend,
    stored_shard_set,
    payload_template: GenerationPayload,
    coding_config: ErasureCodingConfig,
    selected_indices: tuple[int, ...],
) -> tuple[bytes, GenerationPayload]:
    """Read selected stored shards and rebuild the serialized payload.

    The payload record index/offset metadata is retained separately as
    generation metadata. P4 stores shard bytes and shard references; it does
    not currently persist GenerationPayload.records.
    """
    references = {
        reference.shard_index: reference for reference in stored_shard_set.shards
    }

    restored_shards = []
    for index in selected_indices:
        reference = references[index]
        shard_data = backend.get(reference.storage_key)
        storage_object = backend.stat(reference.storage_key)

        # Verify the persisted object against the reference returned by P4.
        assert storage_object.key == reference.storage_key
        assert storage_object.size == reference.size == len(shard_data)
        assert storage_object.sha256 == reference.sha256
        assert hashlib.sha256(shard_data).hexdigest() == reference.sha256

        restored_shards.append(
            EncodedShard(
                index=index,
                data=shard_data,
                sha256=reference.sha256,
            )
        )

    recovered_bytes = ReedSolomonDecoder().decode(
        tuple(restored_shards),
        original_size=stored_shard_set.original_size,
        config=coding_config,
    )

    recovered_payload = GenerationPayload(
        data=recovered_bytes,
        records=payload_template.records,
        format_version=payload_template.format_version,
        sha256=hashlib.sha256(recovered_bytes).hexdigest(),
    )
    return recovered_bytes, recovered_payload


@pytest.fixture
def pipeline_result(tmp_path):
    original = _original_data()
    (
        key,
        chunks,
        encrypted_chunks,
        payload,
        coding_config,
        encoded_shards,
    ) = _run_forward_pipeline(original)

    backend = FilesystemStorageBackend(
        FilesystemStorageConfig(root_dir=tmp_path / "storage")
    )
    stored_shard_set = StorageProcessor(backend).store(
        GENERATION_ID,
        encoded_shards,
    )

    return {
        "original": original,
        "key": key,
        "chunks": chunks,
        "encrypted_chunks": encrypted_chunks,
        "payload": payload,
        "coding_config": coding_config,
        "encoded_shards": encoded_shards,
        "backend": backend,
        "stored_shard_set": stored_shard_set,
        "storage_root": tmp_path / "storage",
    }


def test_full_pipeline_round_trip_through_filesystem(pipeline_result):
    result = pipeline_result
    stored = result["stored_shard_set"]
    encoded = result["encoded_shards"]

    # P4 must return a complete reference set matching P3.
    assert stored.generation_id == GENERATION_ID
    assert stored.original_size == encoded.original_size
    assert stored.data_shards == encoded.data_shards
    assert stored.total_shards == encoded.total_shards
    assert len(stored.shards) == encoded.total_shards

    for reference, shard in zip(stored.shards, encoded.shards, strict=True):
        assert reference.shard_index == shard.index
        assert reference.size == len(shard.data)
        assert reference.sha256 == shard.sha256
        assert result["backend"].get(reference.storage_key) == shard.data

    recovered_bytes, recovered_payload = _restore_from_storage(
        backend=result["backend"],
        stored_shard_set=stored,
        payload_template=result["payload"],
        coding_config=result["coding_config"],
        selected_indices=tuple(range(encoded.total_shards)),
    )

    # P3: the complete serialized generation payload is recovered exactly.
    assert recovered_bytes == result["payload"].data
    assert recovered_payload.records == result["payload"].records

    # P2: parse all serialized encrypted records.
    recovered_records = BinaryPayloadParser().parse_all(recovered_payload)
    original_by_identity = {
        (item.object_id, item.chunk_id): item for item in result["encrypted_chunks"]
    }
    assert len(recovered_records) == len(result["encrypted_chunks"])

    # P1: resolve the expected digest from logical chunk metadata, then decrypt.
    decryptor = AESGCMDecryptor()
    recovered_plaintext_chunks = tuple(
        decryptor.decrypt(
            record,
            key=result["key"],
            expected_plaintext_sha256=original_by_identity[
                (record.object_id, record.chunk_id)
            ].plaintext_sha256,
        )
        for record in recovered_records
    )
    assert recovered_plaintext_chunks == tuple(chunk.data for chunk in result["chunks"])
    assert b"".join(recovered_plaintext_chunks) == result["original"]


def test_pipeline_recovers_from_exactly_k_persisted_shards(pipeline_result):
    result = pipeline_result
    stored = result["stored_shard_set"]
    k = result["coding_config"].data_shards

    # Select a non-contiguous set to ensure shard indexes are respected.
    selected_indices = (0, 2, 4)
    assert len(selected_indices) == k

    recovered_bytes, recovered_payload = _restore_from_storage(
        backend=result["backend"],
        stored_shard_set=stored,
        payload_template=result["payload"],
        coding_config=result["coding_config"],
        selected_indices=selected_indices,
    )

    assert recovered_bytes == result["payload"].data

    records = BinaryPayloadParser().parse_all(recovered_payload)
    digest_by_identity = {
        (item.object_id, item.chunk_id): item.plaintext_sha256
        for item in result["encrypted_chunks"]
    }
    decryptor = AESGCMDecryptor()
    recovered = b"".join(
        decryptor.decrypt(
            item,
            key=result["key"],
            expected_plaintext_sha256=digest_by_identity[
                (item.object_id, item.chunk_id)
            ],
        )
        for item in records
    )
    assert recovered == result["original"]


def test_pipeline_data_survives_new_filesystem_backend_instance(pipeline_result):
    result = pipeline_result

    # Simulate a later process/restart: create a fresh backend instance using
    # only the same storage root and the stored references.
    reopened_backend = FilesystemStorageBackend(
        FilesystemStorageConfig(root_dir=result["storage_root"])
    )

    recovered_bytes, recovered_payload = _restore_from_storage(
        backend=reopened_backend,
        stored_shard_set=result["stored_shard_set"],
        payload_template=result["payload"],
        coding_config=result["coding_config"],
        selected_indices=(1, 2, 3),
    )

    assert recovered_bytes == result["payload"].data

    records = BinaryPayloadParser().parse_all(recovered_payload)
    digest_by_identity = {
        (item.object_id, item.chunk_id): item.plaintext_sha256
        for item in result["encrypted_chunks"]
    }
    decryptor = AESGCMDecryptor()
    recovered = b"".join(
        decryptor.decrypt(
            item,
            key=result["key"],
            expected_plaintext_sha256=digest_by_identity[
                (item.object_id, item.chunk_id)
            ],
        )
        for item in records
    )
    assert recovered == result["original"]
