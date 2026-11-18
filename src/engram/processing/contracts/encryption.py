"""Encryption and decryption stage protocols."""

from typing import Protocol

from engram.processing.artifacts import Chunk, EncryptedChunk, EncryptedChunkRecord


class Encryptor(Protocol):
    def encrypt(
        self,
        chunk: Chunk,
        *,
        object_id: str,
        chunk_id: str,
        key: bytes,
        key_id: str,
    ) -> EncryptedChunk: ...


class Decryptor(Protocol):
    def decrypt(
        self,
        encrypted_record: EncryptedChunkRecord,
        *,
        key: bytes,
        expected_plaintext_sha256: str,
    ) -> bytes: ...
