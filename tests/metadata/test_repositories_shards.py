import pytest


def test_put_get_and_list_shards(metadata_env):
    _, repos = metadata_env

    repos.generations.create(
        "generation-001",
        {"data_shards": 2, "total_shards": 3},
    )
    repos.generations.update_status(
        "generation-001",
        "SEALING",
    )

    for index in range(3):
        repos.shards.put(
            "generation-001",
            index,
            {
                "path": f"shard_{index}.bin",
                "size": 100 + index,
                "checksum": f"checksum-{index}",
            },
        )

    shard = repos.shards.get("generation-001", 1)

    assert shard["shard_index"] == 1
    assert shard["size"] == 101

    shards = repos.shards.list_by_generation("generation-001")

    assert len(shards) == 3
    assert [item["shard_index"] for item in shards] == [0, 1, 2]


def test_shard_requires_generation(metadata_env):
    _, repos = metadata_env

    with pytest.raises(KeyError):
        repos.shards.put(
            "missing-generation",
            0,
            {
                "path": "shard_0.bin",
                "size": 100,
                "checksum": "checksum",
            },
        )
