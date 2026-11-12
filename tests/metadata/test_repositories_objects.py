import pytest


def test_create_and_get_object(metadata_env):
    _, repos = metadata_env

    created = repos.objects.create("object-001")

    assert created["object_id"] == "object-001"
    assert created["status"] == "ACTIVE"
    assert created["latest_version"] == 0

    assert repos.objects.get("object-001") == created


def test_duplicate_object(metadata_env):
    _, repos = metadata_env

    repos.objects.create("object-001")

    with pytest.raises(ValueError):
        repos.objects.create("object-001")


def test_update_object(metadata_env):
    _, repos = metadata_env

    repos.objects.create("object-001")
    updated = repos.objects.update(
        "object-001",
        {"status": "ARCHIVED"},
    )

    assert updated["status"] == "ARCHIVED"


def test_cannot_delete_object_with_versions(metadata_env):
    _, repos = metadata_env

    repos.objects.create("object-001")
    repos.chunks.create(
        "chunk-001",
        "object-001",
        {"status": "AVAILABLE"},
    )

    repos.versions.create(
        "object-001",
        1,
        ["chunk-001"],
    )

    with pytest.raises(ValueError):
        repos.objects.delete("object-001")
