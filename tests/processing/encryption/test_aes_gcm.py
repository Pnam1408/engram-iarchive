import hashlib
from dataclasses import replace

import pytest

from engram.processing.artifacts import Chunk, EncryptedChunkRecord
from engram.processing.encryption import (
    AAD_VERSION,
    ALGORITHM,
    AESGCMDecryptor,
    AESGCMEncryptor,
    AESGCMConfig,
    generate_key,
)
from engram.processing.exceptions import DecryptionError, InvalidConfigurationError


def make_chunk(data=b"some test plaintext"):
    return Chunk(0, 0, len(data), hashlib.sha256(data).hexdigest(), data)


def as_record(encrypted):
    return EncryptedChunkRecord(
        object_id=encrypted.object_id,
        chunk_id=encrypted.chunk_id,
        algorithm=encrypted.algorithm,
        key_id=encrypted.key_id,
        nonce=encrypted.nonce,
        ciphertext=encrypted.ciphertext,
        plaintext_size=encrypted.plaintext_size,
        aad_version=encrypted.aad_version,
    )


def test_round_trip_and_metadata():
    chunk, key = make_chunk(), generate_key()
    encrypted = AESGCMEncryptor().encrypt(
        chunk, object_id="object-1", chunk_id="chunk-1", key=key, key_id="key-1"
    )
    record = as_record(encrypted)

    assert encrypted.algorithm == ALGORITHM
    assert encrypted.aad_version == AAD_VERSION == 2
    assert len(encrypted.nonce) == 12
    assert len(encrypted.ciphertext) == len(chunk.data) + 16
    assert encrypted.plaintext_size == chunk.size
    assert encrypted.plaintext_sha256 == chunk.sha256
    assert (
        AESGCMDecryptor().decrypt(
            record, key=key, expected_plaintext_sha256=chunk.sha256
        )
        == chunk.data
    )


def test_each_encryption_uses_a_fresh_nonce():
    chunk, key = make_chunk(), generate_key()
    encryptor = AESGCMEncryptor()
    kwargs = dict(object_id="o", chunk_id="c", key=key, key_id="k")
    assert (
        encryptor.encrypt(chunk, **kwargs).nonce
        != encryptor.encrypt(chunk, **kwargs).nonce
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("object_id", "other-object"),
        ("chunk_id", "other-chunk"),
        ("algorithm", "AES-128-GCM"),
        ("key_id", "other-key-id"),
        ("plaintext_size", 999),
    ],
)
def test_aad_metadata_tampering_fails_authentication(field, value):
    chunk, key = make_chunk(), generate_key()
    encrypted = AESGCMEncryptor().encrypt(
        chunk, object_id="o", chunk_id="c", key=key, key_id="k"
    )
    tampered = replace(as_record(encrypted), **{field: value})
    with pytest.raises(DecryptionError):
        AESGCMDecryptor().decrypt(
            tampered, key=key, expected_plaintext_sha256=chunk.sha256
        )


def test_ciphertext_tampering_fails_authentication():
    chunk, key = make_chunk(), generate_key()
    encrypted = AESGCMEncryptor().encrypt(
        chunk, object_id="o", chunk_id="c", key=key, key_id="k"
    )
    record = as_record(encrypted)
    altered = bytes([record.ciphertext[0] ^ 1]) + record.ciphertext[1:]
    with pytest.raises(DecryptionError):
        AESGCMDecryptor().decrypt(
            replace(record, ciphertext=altered),
            key=key,
            expected_plaintext_sha256=chunk.sha256,
        )


@pytest.mark.parametrize("key", [b"", b"short", b"x" * 31, b"x" * 33])
def test_invalid_key_size_is_rejected(key):
    with pytest.raises(InvalidConfigurationError):
        AESGCMEncryptor().encrypt(
            make_chunk(), object_id="o", chunk_id="c", key=key, key_id="k"
        )


def test_nonce_tampering_fails_authentication():
    chunk, key = make_chunk(), generate_key()
    encrypted = AESGCMEncryptor().encrypt(
        chunk, object_id="o", chunk_id="c", key=key, key_id="k"
    )
    record = as_record(encrypted)
    altered_nonce = bytes([record.nonce[0] ^ 1]) + record.nonce[1:]
    with pytest.raises(DecryptionError):
        AESGCMDecryptor().decrypt(
            replace(record, nonce=altered_nonce),
            key=key,
            expected_plaintext_sha256=chunk.sha256,
        )


def test_wrong_key_fails_authentication():
    chunk, key = make_chunk(), generate_key()
    encrypted = AESGCMEncryptor().encrypt(
        chunk, object_id="o", chunk_id="c", key=key, key_id="k"
    )
    with pytest.raises(DecryptionError):
        AESGCMDecryptor().decrypt(
            as_record(encrypted),
            key=generate_key(),
            expected_plaintext_sha256=chunk.sha256,
        )


def test_wrong_expected_hash_fails_verification():
    chunk, key = make_chunk(), generate_key()
    encrypted = AESGCMEncryptor().encrypt(
        chunk, object_id="o", chunk_id="c", key=key, key_id="k"
    )
    with pytest.raises(DecryptionError, match="SHA-256"):
        AESGCMDecryptor().decrypt(
            as_record(encrypted), key=key, expected_plaintext_sha256="0" * 64
        )


def test_decryptor_rejects_invalid_record_type():
    with pytest.raises(DecryptionError):
        AESGCMDecryptor().decrypt(
            object(), key=generate_key(), expected_plaintext_sha256="0" * 64
        )


def test_aes_gcm_config_accepts_only_aad_v2():
    assert AESGCMConfig().aad_version == 2
    with pytest.raises(InvalidConfigurationError):
        AESGCMConfig(aad_version=1)
