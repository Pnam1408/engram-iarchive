"""Encrypted chunk artifact; secret key material is never stored here."""

from __future__ import annotations

from dataclasses import dataclass

from ._validation import (
    require_bytes,
    require_non_empty_string,
    require_positive_int,
    require_sha256,
)


@dataclass(frozen=True, slots=True)
class EncryptedChunk:
    object_id: str
    chunk_id: str
    algorithm: str
    key_id: str
    nonce: bytes
    ciphertext: bytes
    plaintext_size: int
    plaintext_sha256: str
    aad_version: int = 2

    def __post_init__(self) -> None:
        require_non_empty_string("object_id", self.object_id)
        require_non_empty_string("chunk_id", self.chunk_id)
        require_non_empty_string("algorithm", self.algorithm)
        require_non_empty_string("key_id", self.key_id)
        require_bytes("nonce", self.nonce)
        require_bytes("ciphertext", self.ciphertext)
        require_positive_int("plaintext_size", self.plaintext_size)
        require_sha256("plaintext_sha256", self.plaintext_sha256)
        require_positive_int("aad_version", self.aad_version)
        from engram.processing.exceptions import InvalidArtifactError

        if not self.nonce:
            raise InvalidArtifactError("nonce must not be empty")
        if not self.ciphertext:
            raise InvalidArtifactError("ciphertext must not be empty")
