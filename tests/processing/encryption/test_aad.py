import pytest

from engram.processing.encryption import build_aad
from engram.processing.exceptions import InvalidConfigurationError


def test_aad_is_deterministic_and_identity_bound():
    first = build_aad(object_id="object-1", chunk_id="chunk-1")
    assert first == build_aad(object_id="object-1", chunk_id="chunk-1")
    assert first != build_aad(object_id="object-2", chunk_id="chunk-1")
    assert first != build_aad(object_id="object-1", chunk_id="chunk-2")


def test_length_prefix_avoids_separator_ambiguity():
    assert build_aad(object_id="a:b", chunk_id="c") != build_aad(object_id="a", chunk_id="b:c")


def test_unsupported_aad_version_is_rejected():
    with pytest.raises(InvalidConfigurationError):
        build_aad(object_id="o", chunk_id="c", version=2)
