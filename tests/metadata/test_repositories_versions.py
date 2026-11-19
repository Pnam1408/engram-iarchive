import pytest

from engram.metadata.database.column_families import ColumnFamily
from engram.metadata.database.key_codec import KeyCodec
from tests.metadata.conftest import make_chunks_available


def prepare_object_with_chunks(repos):
    repos.objects.create("object-001")

    repos.chunks.create("chunk-001", "object-001")
    repos.chunks.create("chunk-002", "object-001")

    make_chunks_available(
        repos,
        "object-001",
        ["chunk-001", "chunk-002"],
        generation_id="generation-version-test",
    )


def test_create_and_get_version(metadata_env):
    _, repos = metadata_env
    prepare_object_with_chunks(repos)

    manifest = repos.versions.create(
        "object-001",
        1,
        ["chunk-001", "chunk-002"],
    )

    assert manifest["version"] == 1
    assert manifest["chunk_count"] == 2

    assert repos.versions.get_chunks(
        "object-001",
        1,
    ) == ["chunk-001", "chunk-002"]

    assert repos.objects.get("object-001")["latest_version"] == 1


def test_list_versions(metadata_env):
    db, repos = metadata_env
    prepare_object_with_chunks(repos)

    repos.versions.create("object-001", 1, ["chunk-001"])
    repos.versions.create("object-001", 2, ["chunk-002"])

    assert repos.versions.list_versions("object-001") == [1, 2]


def test_reference_count(metadata_env):
    _, repos = metadata_env
    prepare_object_with_chunks(repos)

    repos.versions.create("object-001", 1, ["chunk-001"])
    repos.versions.create("object-001", 2, ["chunk-001"])

    assert repos.chunks.get_reference_count("chunk-001") == 2


def test_reject_chunk_from_another_object(metadata_env):
    _, repos = metadata_env

    repos.objects.create("object-001")
    repos.objects.create("object-002")

    repos.chunks.create(
        "chunk-002",
        "object-002",
    )

    with pytest.raises(ValueError):
        repos.versions.create(
            "object-001",
            1,
            ["chunk-002"],
        )


def test_reject_non_available_chunk(metadata_env):
    _, repos = metadata_env

    repos.objects.create("object-001")
    repos.chunks.create("chunk-001", "object-001")

    with pytest.raises(ValueError):
        repos.versions.create(
            "object-001",
            1,
            ["chunk-001"],
        )
