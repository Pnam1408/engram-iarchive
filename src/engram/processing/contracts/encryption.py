"""Encryption and decryption stage protocols."""

from typing import Protocol

from engram.processing.artifacts import Chunk, EncryptedChunk


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
    def decrypt(self, encrypted_chunk: EncryptedChunk, *, key: bytes) -> bytes: ...
