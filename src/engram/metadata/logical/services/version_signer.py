from typing import Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


class VersionSigner(Protocol):
    """Signing interface consumed by VersionBuilder."""

    @property
    def public_key(self) -> bytes:
        """Return the raw public key bytes."""
        ...

    def sign(self, payload: bytes) -> bytes:
        """Sign canonical payload bytes."""
        ...


class Ed25519VersionSigner:
    """Ed25519 signer backed by cryptography's raw 32-byte private key."""

    def __init__(self, private_key: Ed25519PrivateKey) -> None:
        if not isinstance(private_key, Ed25519PrivateKey):
            raise TypeError("private_key must be an Ed25519PrivateKey")
        self._private_key = private_key

    @classmethod
    def generate(cls) -> "Ed25519VersionSigner":
        return cls(Ed25519PrivateKey.generate())

    @classmethod
    def from_private_bytes(cls, private_key: bytes) -> "Ed25519VersionSigner":
        if not isinstance(private_key, bytes):
            raise TypeError("private_key must be bytes")
        if len(private_key) != 32:
            raise ValueError("Ed25519 private key must contain exactly 32 bytes")
        return cls(Ed25519PrivateKey.from_private_bytes(private_key))

    @property
    def public_key(self) -> bytes:
        return self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    def sign(self, payload: bytes) -> bytes:
        if not isinstance(payload, bytes):
            raise TypeError("payload must be bytes")
        return self._private_key.sign(payload)


def verify_ed25519_signature(
    public_key: bytes,
    payload: bytes,
    signature: bytes,
) -> bool:
    """Return False for malformed keys or invalid signatures."""
    if not all(isinstance(value, bytes) for value in (public_key, payload, signature)):
        return False
    if len(public_key) != 32:
        return False
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, payload)
    except (ValueError, InvalidSignature):
        return False
    return True
