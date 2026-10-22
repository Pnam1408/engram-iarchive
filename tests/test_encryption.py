
import pytest

from cryptography.exceptions import InvalidTag

from engram.encryption import (
    EncryptedPayload,
    generate_key,
    encrypt,
    decrypt,
)


def test_encrypt_decrypt_roundtrip():
    key = generate_key()
    plaintext = b"Engram incremental storage simulation"

    payload = encrypt(plaintext, key)

    assert payload.ciphertext != plaintext
    assert decrypt(payload, key) == plaintext


def test_encrypt_empty_data():
    key = generate_key()

    payload = encrypt(b"", key)

    assert decrypt(payload, key) == b""


def test_wrong_key_fails():
    payload = encrypt(b"secret data", generate_key())

    with pytest.raises(InvalidTag):
        decrypt(payload, generate_key())


def test_tampered_ciphertext_fails():
    key = generate_key()
    payload = encrypt(b"important data", key)

    modified = bytearray(payload.ciphertext)
    modified[0] ^= 1

    tampered = EncryptedPayload(
        ciphertext=bytes(modified),
        nonce=payload.nonce,
    )

    with pytest.raises(InvalidTag):
        decrypt(tampered, key)


def test_wrong_associated_data_fails():
    key = generate_key()

    payload = encrypt(
        b"chunk data",
        key,
        associated_data=b"chunk-001",
    )

    with pytest.raises(InvalidTag):
        decrypt(
            payload,
            key,
            associated_data=b"chunk-002",
        )
