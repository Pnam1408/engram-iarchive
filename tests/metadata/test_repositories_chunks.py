import pytest


def test_chunk_reference_count(metadata_env):
    _, repos = metadata_env

    repos.objects.create("object-001")
    repos.chunks.create("chunk-001", "object-001")

    assert repos.chunks.get_reference_count("chunk-001") == 0

    assert repos.chunks.increment_ref("chunk-001") == 1
    assert repos.chunks.increment_ref("chunk-001", 2) == 3

    assert repos.chunks.decrement_ref("chunk-001") == 2
    assert repos.chunks.get_reference_count("chunk-001") == 2


def test_reference_count_cannot_be_negative(metadata_env):
    _, repos = metadata_env

    repos.objects.create("object-001")
    repos.chunks.create("chunk-001", "object-001")

    with pytest.raises(ValueError):
        repos.chunks.decrement_ref("chunk-001")
