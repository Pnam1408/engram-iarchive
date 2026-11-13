"""Encryption implementations for the modular processing pipeline."""

from .aad import AAD_VERSION, build_aad
from .aes_gcm import ALGORITHM, KEY_SIZE, NONCE_SIZE, TAG_SIZE, AESGCMDecryptor, AESGCMEncryptor, generate_key
from .base import Decryptor, Encryptor
from .config import AESGCMConfig

__all__ = [
    "AAD_VERSION", "ALGORITHM", "KEY_SIZE", "NONCE_SIZE", "TAG_SIZE",
    "AESGCMConfig", "AESGCMEncryptor", "AESGCMDecryptor",
    "Encryptor", "Decryptor", "build_aad", "generate_key",
]
