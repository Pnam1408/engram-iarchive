import hashlib

import pytest

from engram.processing.artifacts import Chunk, EncryptedChunk
from engram.processing.encryption import (
    ALGORITHM,
    AESGCMDecryptor,
    AESGCMEncryptor,
    AESGCMConfig,
    generate_key,
)
from engram.processing.exceptions import DecryptionError, InvalidConfigurationError


def make_chunk(data=b"some test plaintext"):
    return Chunk(0, 0, len(data), hashlib.sha256(data).hexdigest(), data)


def test_round_trip_and_metadata():
    chunk = make_chunk()
    key = generate_key()
    encrypted = AESGCMEncryptor().encrypt(
        chunk, object_id="object-1", chunk_id="chunk-1", key=key, key_id="key-1"
    )

    assert encrypted.algorithm == ALGORITHM
    assert encrypted.aad_version == 1
    assert len(encrypted.nonce) == 12
    assert len(encrypted.ciphertext) == len(chunk.data) + 16
    assert encrypted.plaintext_size == chunk.size
    assert encrypted.plaintext_sha256 == chunk.sha256
    assert AESGCMDecryptor().decrypt(encrypted, key=key) == chunk.data


def test_each_encryption_uses_a_fresh_nonce():
    chunk, key = make_chunk(), generate_key()
    encryptor = AESGCMEncryptor()
    kwargs = dict(object_id="o", chunk_id="c", key=key, key_id="k")
    assert encryptor.encrypt(chunk, **kwargs).nonce != encryptor.encrypt(chunk, **kwargs).nonce


@pytest.mark.parametrize("field,value", [("object_id", "other-object"), ("chunk_id", "other-chunk")])
def test_identity_tampering_fails_authentication(field, value):
    chunk, key = make_chunk(), generate_key()
    encrypted = AESGCMEncryptor().encrypt(
        chunk, object_id="o", chunk_id="c", key=key, key_id="k"
    )
    changed = dict(encrypted.__dict__) if hasattr(encrypted, "__dict__") else {
        name: getattr(encrypted, name) for name in encrypted.__dataclass_fields__
    }
    changed[field] = value
    tampered = EncryptedChunk(**changed)
    with pytest.raises(DecryptionError):
        AESGCMDecryptor().decrypt(tampered, key=key)


def test_ciphertext_tampering_fails_authentication():
    chunk, key = make_chunk(), generate_key()
    encrypted = AESGCMEncryptor().encrypt(
        chunk, object_id="o", chunk_id="c", key=key, key_id="k"
    )
    altered = bytes([encrypted.ciphertext[0] ^ 1]) + encrypted.ciphertext[1:]
    changed = {name: getattr(encrypted, name) for name in encrypted.__dataclass_fields__}
    changed["ciphertext"] = altered
    with pytest.raises(DecryptionError):
        AESGCMDecryptor().decrypt(EncryptedChunk(**changed), key=key)


@pytest.mark.parametrize("key", [b"", b"short", b"x" * 31, b"x" * 33])
def test_invalid_key_size_is_rejected(key):
    chunk = make_chunk()
    with pytest.raises(InvalidConfigurationError):
        AESGCMEncryptor().encrypt(chunk, object_id="o", chunk_id="c", key=key, key_id="k")


def test_wrong_key_fails_authentication():
    chunk, key = make_chunk(), generate_key()
    encrypted = AESGCMEncryptor().encrypt(
        chunk, object_id="o", chunk_id="c", key=key, key_id="k"
    )
    with pytest.raises(DecryptionError):
        AESGCMDecryptor().decrypt(encrypted, key=generate_key())


def test_unsupported_algorithm_is_rejected():
    chunk, key = make_chunk(), generate_key()
    encrypted = AESGCMEncryptor().encrypt(
        chunk, object_id="o", chunk_id="c", key=key, key_id="k"
    )
    changed = {name: getattr(encrypted, name) for name in encrypted.__dataclass_fields__}
    changed["algorithm"] = "AES-128-GCM"
    with pytest.raises(DecryptionError):
        AESGCMDecryptor().decrypt(EncryptedChunk(**changed), key=key)


def test_empty_plaintext_chunk_not_supported_by_chunk_contract():
    with pytest.raises(ValueError):
        Chunk(0, 0, 0, hashlib.sha256(b"").hexdigest(), b"")
