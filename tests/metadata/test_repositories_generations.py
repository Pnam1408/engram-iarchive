import pytest


def test_generation_lifecycle(metadata_env):
    _, repos = metadata_env

    repos.objects.create("object-001")
    repos.chunks.create(
        "chunk-001",
        "object-001",
    )

    generation = repos.generations.create(
        "generation-001",
        {"data_shards": 2, "total_shards": 3},
    )

    assert generation["status"] == "OPEN"

    repos.generations.add_record(
        "generation-001",
        "object-001",
        "chunk-001",
        record_offset=0,
        record_length=120,
    )

    records = repos.generations.list_chunks("generation-001")
    assert len(records) == 1
    assert records[0]["chunk_id"] == "chunk-001"

    repos.generations.update_status("generation-001", "SEALING")

    for index in range(2):
        repos.shards.put(
            "generation-001",
            index,
            {
                "path": f"generations/generation-001/shard_{index}.bin",
                "size": 100,
                "checksum": f"checksum-{index}",
            },
        )

    repos.generations.update_status("generation-001", "STORED")
    repos.generations.update_status("generation-001", "AVAILABLE")

    assert repos.generations.get("generation-001")["status"] == "AVAILABLE"

    assert repos.chunks.get("chunk-001")["status"] == "AVAILABLE"


def test_invalid_generation_transition(metadata_env):
    _, repos = metadata_env

    repos.generations.create(
        "generation-001",
        {
            "data_shards": 2,
            "total_shards": 3,
        },
    )

    with pytest.raises(ValueError):
        repos.generations.update_status(
            "generation-001",
            "AVAILABLE",
        )


def test_cannot_mark_stored_without_enough_shards(metadata_env):
    _, repos = metadata_env

    repos.objects.create("object-001")
    repos.chunks.create("chunk-001", "object-001")

    repos.generations.create(
        "generation-001",
        {"data_shards": 2, "total_shards": 3},
    )

    repos.generations.add_record(
        "generation-001",
        "object-001",
        "chunk-001",
        0,
        100,
    )

    repos.generations.update_status("generation-001", "SEALING")

    repos.shards.put(
        "generation-001",
        0,
        {
            "path": "shard_0.bin",
            "size": 100,
            "checksum": "checksum-0",
        },
    )

    with pytest.raises(ValueError):
        repos.generations.update_status(
            "generation-001",
            "STORED",
        )
