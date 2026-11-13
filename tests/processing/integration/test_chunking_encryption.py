"""Integration tests for the new chunking -> AES-GCM processing stages.

These tests intentionally exercise only ``engram.processing``. Legacy
``engram.chunking`` and ``engram.encryption`` modules are not imported.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from types import SimpleNamespace

import pytest

from engram.processing.artifacts import Chunk, EncryptedChunk
from engram.processing.chunking import (
    FastCDCChunker,
    FastCDCConfig,
    FixedSizeChunker,
)
from engram.processing.encryption import (
    AESGCMDecryptor,
    AESGCMEncryptor,
    generate_key,
)
from engram.processing.exceptions import DecryptionError


OBJECT_ID = "integration-object-001"
KEY_ID = "integration-key-001"


def _sample_file() -> bytes:
    """Return deterministic binary data with null and non-UTF8 bytes."""
    prefix = bytes(range(256))
    return (prefix * 19) + b"\x00ENGRAM\xff\xfe\x80" + (prefix[::-1] * 7)


def _encrypt_chunks(
    chunks: tuple[Chunk, ...], *, object_id: str, key: bytes
) -> tuple[EncryptedChunk, ...]:
    encryptor = AESGCMEncryptor()
    encrypted: list[EncryptedChunk] = []

    for chunk in chunks:
        encrypted.append(
            encryptor.encrypt(
                chunk,
                object_id=object_id,
                chunk_id=f"{object_id}:chunk:{chunk.index}",
                key=key,
                key_id=KEY_ID,
            )
        )

    return tuple(encrypted)


def _decrypt_and_reconstruct(
    encrypted_chunks: tuple[EncryptedChunk, ...], *, key: bytes
) -> bytes:
    decryptor = AESGCMDecryptor()
    # The current Chunking contract assigns indexes in input order. Sorting
    # explicitly documents that reconstruction follows logical chunk order,
    # not encryption completion or collection order.
    ordered = sorted(encrypted_chunks, key=lambda item: int(item.chunk_id.rsplit(":", 1)[1]))
    return b"".join(decryptor.decrypt(item, key=key) for item in ordered)


def _assert_chunk_layout(chunks: tuple[Chunk, ...], original: bytes) -> None:
    assert chunks
    assert [chunk.index for chunk in chunks] == list(range(len(chunks)))

    expected_offset = 0
    for chunk in chunks:
        assert chunk.offset == expected_offset
        assert chunk.size == len(chunk.data)
        assert hashlib.sha256(chunk.data).hexdigest() == chunk.sha256
        assert original[chunk.offset : chunk.end] == chunk.data
        expected_offset = chunk.end

    assert expected_offset == len(original)
    assert b"".join(chunk.data for chunk in chunks) == original


def _assert_encrypted_metadata(
    chunks: tuple[Chunk, ...], encrypted: tuple[EncryptedChunk, ...]
) -> None:
    assert len(encrypted) == len(chunks)
    for chunk, encrypted_chunk in zip(chunks, encrypted, strict=True):
        assert encrypted_chunk.object_id == OBJECT_ID
        assert encrypted_chunk.chunk_id == f"{OBJECT_ID}:chunk:{chunk.index}"
        assert encrypted_chunk.key_id == KEY_ID
        assert encrypted_chunk.plaintext_size == chunk.size
        assert encrypted_chunk.plaintext_sha256 == chunk.sha256


@pytest.mark.parametrize(
    "chunker",
    [
        pytest.param(FixedSizeChunker(chunk_size=257), id="fixed-size"),
    ],
)
def test_fixed_size_chunking_to_encryption_round_trip(chunker):
    original = _sample_file()
    key = generate_key()

    chunks = chunker.chunk(original)
    _assert_chunk_layout(chunks, original)

    encrypted = _encrypt_chunks(chunks, object_id=OBJECT_ID, key=key)
    _assert_encrypted_metadata(chunks, encrypted)

    recovered = _decrypt_and_reconstruct(encrypted, key=key)
    assert recovered == original


def test_fastcdc_adapter_to_encryption_round_trip(monkeypatch):
    """Exercise FastCDC artifact construction and encryption without Rust.

    Native FastCDC boundary generation is separately covered by the native
    extension/build tests. This test isolates the processing adapter contract
    and the downstream encryption integration.
    """
    original = _sample_file()
    config = FastCDCConfig(min_size=64, avg_size=128, max_size=256)
    chunker = FastCDCChunker(config)

    # Deterministic representative boundaries supplied at the native adapter
    # seam. The last chunk is allowed to be smaller than min_size.
    boundaries = []
    offset = 0
    while offset < len(original):
        size = min(256, len(original) - offset)
        boundaries.append((offset, size))
        offset += size

    fake_native = SimpleNamespace(
        chunk_boundaries=lambda data, min_size, avg_size, max_size: boundaries
    )
    monkeypatch.setattr(chunker, "_load_native_extension", lambda: fake_native)

    key = generate_key()
    chunks = chunker.chunk(original)
    _assert_chunk_layout(chunks, original)

    encrypted = _encrypt_chunks(chunks, object_id=OBJECT_ID, key=key)
    _assert_encrypted_metadata(chunks, encrypted)

    assert _decrypt_and_reconstruct(encrypted, key=key) == original


def test_each_chunk_can_be_decrypted_independently():
    original = _sample_file()
    key = generate_key()
    chunks = FixedSizeChunker(chunk_size=333).chunk(original)
    encrypted = _encrypt_chunks(chunks, object_id=OBJECT_ID, key=key)
    decryptor = AESGCMDecryptor()

    for chunk, encrypted_chunk in zip(chunks, encrypted, strict=True):
        assert decryptor.decrypt(encrypted_chunk, key=key) == chunk.data


def test_chunk_cannot_be_moved_to_another_object_or_chunk_identity():
    original = _sample_file()
    key = generate_key()
    chunk = FixedSizeChunker(chunk_size=512).chunk(original)[0]
    encrypted = AESGCMEncryptor().encrypt(
        chunk,
        object_id=OBJECT_ID,
        chunk_id=f"{OBJECT_ID}:chunk:0",
        key=key,
        key_id=KEY_ID,
    )
    decryptor = AESGCMDecryptor()

    with pytest.raises(DecryptionError):
        decryptor.decrypt(replace(encrypted, object_id="another-object"), key=key)

    with pytest.raises(DecryptionError):
        decryptor.decrypt(replace(encrypted, chunk_id="another-chunk"), key=key)


def test_empty_input_produces_no_chunks_and_no_encrypted_records():
    key = generate_key()
    chunks = FixedSizeChunker(chunk_size=128).chunk(b"")

    assert chunks == ()
    assert _encrypt_chunks(chunks, object_id=OBJECT_ID, key=key) == ()
    assert _decrypt_and_reconstruct((), key=key) == b""
