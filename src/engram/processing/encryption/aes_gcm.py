"""AES-256-GCM implementation for the processing pipeline."""

from __future__ import annotations

import hashlib
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from engram.processing.artifacts import Chunk, EncryptedChunk
from engram.processing.exceptions import (
    DecryptionError,
    EncryptionError,
    InvalidConfigurationError,
)

from .aad import build_aad
from .config import AESGCMConfig

ALGORITHM = "AES-256-GCM"
KEY_SIZE = 32
NONCE_SIZE = 12
TAG_SIZE = 16


def generate_key() -> bytes:
    """Generate a fresh 256-bit AES key."""
    return AESGCM.generate_key(bit_length=256)


def _validate_key(key: bytes) -> None:
    if not isinstance(key, bytes) or len(key) != KEY_SIZE:
        raise InvalidConfigurationError("AES-256-GCM key must be exactly 32 bytes")


class AESGCMEncryptor:
    def __init__(self, config: AESGCMConfig | None = None) -> None:
        self._config = config or AESGCMConfig()

    def encrypt(
        self,
        chunk: Chunk,
        *,
        object_id: str,
        chunk_id: str,
        key: bytes,
        key_id: str,
    ) -> EncryptedChunk:
        _validate_key(key)
        if not isinstance(chunk, Chunk):
            raise EncryptionError("chunk must be a processing Chunk artifact")
        if not isinstance(key_id, str) or not key_id:
            raise InvalidConfigurationError("key_id must be a non-empty string")

        aad = build_aad(
            object_id=object_id,
            chunk_id=chunk_id,
            version=self._config.aad_version,
        )
        nonce = os.urandom(NONCE_SIZE)
        try:
            ciphertext = AESGCM(key).encrypt(nonce, chunk.data, aad)
            return EncryptedChunk(
                object_id=object_id,
                chunk_id=chunk_id,
                algorithm=ALGORITHM,
                key_id=key_id,
                nonce=nonce,
                ciphertext=ciphertext,
                plaintext_size=chunk.size,
                plaintext_sha256=chunk.sha256,
                aad_version=self._config.aad_version,
            )
        except (TypeError, ValueError) as exc:
            raise EncryptionError("AES-256-GCM encryption failed") from exc


class AESGCMDecryptor:
    def decrypt(self, encrypted_chunk: EncryptedChunk, *, key: bytes) -> bytes:
        _validate_key(key)
        if not isinstance(encrypted_chunk, EncryptedChunk):
            raise DecryptionError("encrypted_chunk must be an EncryptedChunk artifact")
        if encrypted_chunk.algorithm != ALGORITHM:
            raise DecryptionError(
                f"Unsupported encryption algorithm: {encrypted_chunk.algorithm}"
            )
        if len(encrypted_chunk.nonce) != NONCE_SIZE:
            raise DecryptionError("AES-256-GCM nonce must be exactly 12 bytes")
        if len(encrypted_chunk.ciphertext) < TAG_SIZE:
            raise DecryptionError(
                "Ciphertext is shorter than the GCM authentication tag"
            )

        try:
            aad = build_aad(
                object_id=encrypted_chunk.object_id,
                chunk_id=encrypted_chunk.chunk_id,
                version=encrypted_chunk.aad_version,
            )
            plaintext = AESGCM(key).decrypt(
                encrypted_chunk.nonce, encrypted_chunk.ciphertext, aad
            )
        except InvalidTag as exc:
            raise DecryptionError("AES-GCM authentication failed") from exc
        except (TypeError, ValueError, InvalidConfigurationError) as exc:
            raise DecryptionError("Encrypted chunk metadata or AAD is invalid") from exc

        if len(plaintext) != encrypted_chunk.plaintext_size:
            raise DecryptionError("Decrypted plaintext size does not match metadata")
        digest = hashlib.sha256(plaintext).hexdigest()
        if digest != encrypted_chunk.plaintext_sha256:
            raise DecryptionError("Decrypted plaintext SHA-256 does not match metadata")
        return plaintext
