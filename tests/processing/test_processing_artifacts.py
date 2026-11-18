from __future__ import annotations

import hashlib

import pytest

from engram.processing.artifacts import (
    Chunk,
    EncodedShard,
    EncodedShardSet,
    EncryptedChunk,
    EncryptedChunkRecord,
    GenerationPayload,
    PayloadRecord,
)
from engram.processing.exceptions import InvalidArtifactError


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_chunk_validates_size_and_digest() -> None:
    chunk = Chunk(0, 0, 3, digest(b"abc"), b"abc")
    assert chunk.end == 3

    with pytest.raises(InvalidArtifactError):
        Chunk(0, 0, 4, digest(b"abc"), b"abc")

    with pytest.raises(InvalidArtifactError):
        Chunk(0, 0, 3, digest(b"xyz"), b"abc")


def test_encrypted_chunk_does_not_contain_secret_key_and_validates_fields() -> None:
    artifact = EncryptedChunk(
        object_id="object-1",
        chunk_id="chunk-1",
        algorithm="AES-256-GCM",
        key_id="key-1",
        nonce=b"n" * 12,
        ciphertext=b"ciphertext-and-tag",
        plaintext_size=3,
        plaintext_sha256=digest(b"abc"),
    )
    assert artifact.key_id == "key-1"
    assert not hasattr(artifact, "key")

    with pytest.raises(InvalidArtifactError):
        EncryptedChunk(
            "", "chunk-1", "AES-256-GCM", "key-1", b"n", b"c", 3, digest(b"abc")
        )


def test_encrypted_chunk_record_contains_no_plaintext_digest_or_secret_key() -> None:
    record = EncryptedChunkRecord(
        object_id="object-1",
        chunk_id="chunk-1",
        algorithm="AES-256-GCM",
        key_id="key-1",
        nonce=b"n" * 12,
        ciphertext=b"ciphertext-and-tag",
        plaintext_size=3,
    )
    assert record.aad_version == 2
    assert not hasattr(record, "plaintext_sha256")
    assert not hasattr(record, "key")

    with pytest.raises(InvalidArtifactError):
        EncryptedChunkRecord(
            "",
            "chunk-1",
            "AES-256-GCM",
            "key-1",
            b"n" * 12,
            b"ciphertext",
            3,
        )


def test_payload_records_must_be_contiguous_and_cover_data() -> None:
    data = b"header-record-1record-2"
    records = (
        PayloadRecord(0, "object-1", "chunk-1", 0, 13),
        PayloadRecord(1, "object-2", "chunk-2", 13, 10),
    )
    payload = GenerationPayload(data, records, 1, digest(data))
    assert payload.records[1].offset == 13

    bad_records = (
        records[0],
        PayloadRecord(1, "object-2", "chunk-2", 14, 9),
    )
    with pytest.raises(InvalidArtifactError):
        GenerationPayload(data, bad_records, 1, digest(data))


def test_payload_rejects_bad_digest_and_out_of_range_record() -> None:
    with pytest.raises(InvalidArtifactError):
        GenerationPayload(b"abc", (), 1, digest(b"wrong"))

    with pytest.raises(InvalidArtifactError):
        GenerationPayload(
            b"abc",
            (PayloadRecord(0, "o", "c", 0, 4),),
            1,
            digest(b"abc"),
        )


def test_empty_generation_payload_is_valid() -> None:
    payload = GenerationPayload(b"", (), 1, digest(b""))
    assert payload.records == ()


def test_encoded_shard_validates_digest() -> None:
    shard = EncodedShard(0, b"data", digest(b"data"))
    assert shard.index == 0

    with pytest.raises(InvalidArtifactError):
        EncodedShard(0, b"data", digest(b"other"))


def test_encoded_shard_set_requires_exact_ordered_indexes_and_equal_sizes() -> None:
    shards = tuple(
        EncodedShard(i, bytes([i]) * 4, digest(bytes([i]) * 4)) for i in range(3)
    )
    result = EncodedShardSet(shards, 8, 2, 3)
    assert result.total_shards == 3

    with pytest.raises(InvalidArtifactError):
        EncodedShardSet((shards[0], shards[2], shards[1]), 8, 2, 3)

    uneven = (
        shards[0],
        EncodedShard(1, b"x", digest(b"x")),
        shards[2],
    )
    with pytest.raises(InvalidArtifactError):
        EncodedShardSet(uneven, 8, 2, 3)
