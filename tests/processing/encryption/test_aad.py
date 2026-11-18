import pytest

from engram.processing.encryption import build_aad
from engram.processing.exceptions import InvalidConfigurationError


def make_aad(**overrides):
    values = dict(
        object_id="object-1",
        chunk_id="chunk-1",
        algorithm="AES-256-GCM",
        key_id="key-1",
        plaintext_size=123,
    )
    values.update(overrides)
    return build_aad(**values)


def test_aad_is_deterministic_and_binds_all_metadata():
    first = make_aad()
    assert first == make_aad()
    for field, value in (
        ("object_id", "object-2"),
        ("chunk_id", "chunk-2"),
        ("algorithm", "AES-128-GCM"),
        ("key_id", "key-2"),
        ("plaintext_size", 124),
    ):
        assert first != make_aad(**{field: value})


def test_length_prefix_avoids_separator_ambiguity():
    assert make_aad(object_id="a:b", chunk_id="c") != make_aad(
        object_id="a", chunk_id="b:c"
    )


def test_unsupported_aad_version_is_rejected():
    with pytest.raises(InvalidConfigurationError):
        make_aad(version=1)
