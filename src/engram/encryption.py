
from dataclasses import dataclass
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


NONCE_SIZE = 12
KEY_SIZE = 32  # 256 bits


@dataclass(frozen=True)
class EncryptedPayload:
    """Kết quả mã hóa, không chứa khóa bí mật."""

    ciphertext: bytes
    nonce: bytes


def generate_key() -> bytes:
    """Sinh khóa AES-256 ngẫu nhiên."""
    return AESGCM.generate_key(bit_length=256)


def encrypt(
    plaintext: bytes,
    key: bytes,
    associated_data: bytes | None = None,
) -> EncryptedPayload:
    """
    Mã hóa plaintext bằng AES-256-GCM.

    associated_data (AAD) là metadata tùy chọn được xác thực
    nhưng không bị mã hóa.
    """
    if not isinstance(plaintext, bytes):
        raise TypeError("plaintext must be bytes")

    if not isinstance(key, bytes) or len(key) != KEY_SIZE:
        raise ValueError("AES-256 key must be exactly 32 bytes")

    if associated_data is not None and not isinstance(
        associated_data, bytes
    ):
        raise TypeError("associated_data must be bytes or None")

    nonce = os.urandom(NONCE_SIZE)
    aesgcm = AESGCM(key)

    ciphertext = aesgcm.encrypt(
        nonce,
        plaintext,
        associated_data,
    )

    return EncryptedPayload(
        ciphertext=ciphertext,
        nonce=nonce,
    )


def decrypt(
    payload: EncryptedPayload,
    key: bytes,
    associated_data: bytes | None = None,
) -> bytes:
    """
    Giải mã và xác thực ciphertext.

    Nếu key, nonce, ciphertext, tag hoặc AAD không hợp lệ,
    cryptography.exceptions.InvalidTag sẽ được phát sinh.
    """
    if not isinstance(key, bytes) or len(key) != KEY_SIZE:
        raise ValueError("AES-256 key must be exactly 32 bytes")

    if len(payload.nonce) != NONCE_SIZE:
        raise ValueError("Invalid nonce length")

    aesgcm = AESGCM(key)

    return aesgcm.decrypt(
        payload.nonce,
        payload.ciphertext,
        associated_data,
    )
