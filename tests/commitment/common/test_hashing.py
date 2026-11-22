import hashlib
import pytest

from engram.commitment.common.hashing import hash_bytes, hash_with_domain
from engram.commitment.common.encoding import encode_string
from engram.commitment.common.exceptions import InvalidTypeError, InvalidInputError


def test_hash_bytes_sha256_vector():
    assert hash_bytes(b"abc") == hashlib.sha256(b"abc").digest()


def test_hash_with_domain_matches_defined_framing():
    payload = b"payload"
    domain = "ENGRAM/TEST/v1"
    assert (
        hash_with_domain(domain, payload)
        == hashlib.sha256(encode_string(domain) + payload).digest()
    )


def test_domain_separation_changes_digest():
    assert hash_with_domain("A", b"same") != hash_with_domain("B", b"same")


def test_data_change_changes_digest():
    assert hash_with_domain("A", b"one") != hash_with_domain("A", b"two")


def test_rejects_invalid_types_and_empty_domain():
    with pytest.raises(InvalidTypeError):
        hash_bytes(bytearray(b"abc"))
    with pytest.raises(InvalidTypeError):
        hash_with_domain("D", bytearray(b"abc"))
    with pytest.raises(InvalidInputError):
        hash_with_domain("", b"abc")
