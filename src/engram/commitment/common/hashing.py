"""SHA-256 helpers with optional explicit domain separation."""

import hashlib
from .encoding import encode_string
from .validation import require_bytes, require_string


def hash_bytes(data: bytes) -> bytes:
    return hashlib.sha256(require_bytes(data, name="data")).digest()


def hash_with_domain(domain: str, data: bytes) -> bytes:
    domain_text = require_string(domain, name="domain", allow_empty=False)
    payload = require_bytes(data, name="data")
    return hashlib.sha256(encode_string(domain_text) + payload).digest()
