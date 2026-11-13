import pytest


def test_put_get_and_list_shards(metadata_env):
    _, repos = metadata_env

    # Tạo object và chunk
    repos.objects.create("object-001")
    repos.chunks.create("chunk-001", "object-001")

    # Tạo generation
    repos.generations.create(
        "generation-001",
        {"data_shards": 2, "total_shards": 3},
    )

    # Generation phải có ít nhất một record trước khi seal
    repos.generations.add_record(
        generation_id="generation-001",
        chunk_id="chunk-001",
        object_id="object-001",
        record_offset=0,
        record_length=100,
    )

    repos.generations.update_status(
        "generation-001",
        "SEALING",
    )

    shard_data = [
        {
            "shard_index": 0,
            "metadata": {
                "path": "generation-001/shard-0",
                "size": 100,
                "checksum": "checksum-0",
            },
        },
        {
            "shard_index": 1,
            "metadata": {
                "path": "generation-001/shard-1",
                "size": 100,
                "checksum": "checksum-1",
            },
        },
        {
            "shard_index": 2,
            "metadata": {
                "path": "generation-001/shard-2",
                "size": 100,
                "checksum": "checksum-2",
            },
        },
    ]

    for shard in shard_data:
        repos.shards.put(
            "generation-001",
            shard["shard_index"],
            shard["metadata"],
        )

    shards = repos.shards.list_by_generation("generation-001")

    assert len(shards) == 3
    assert [s["shard_index"] for s in shards] == [0, 1, 2]


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
