import pytest

from engram.metadata import MetadataDB, MetadataRepositories


@pytest.fixture
def metadata_env(tmp_path):
    with MetadataDB(tmp_path / "metadata") as db:
        yield db, MetadataRepositories(db)


def make_chunks_available(
    repos,
    object_id: str,
    chunk_ids: list[str],
    generation_id: str = "generation-test",
):
    """Đưa các chunk PENDING qua generation lifecycle đến AVAILABLE."""

    if not chunk_ids:
        raise ValueError("chunk_ids must not be empty")

    data_shards = 2
    total_shards = 3

    repos.generations.create(
        generation_id,
        {
            "data_shards": data_shards,
            "total_shards": total_shards,
        },
    )

    offset = 0

    for chunk_id in chunk_ids:
        # Test dùng record_length cố định để mô phỏng vị trí
        # các record trong generation payload.
        record_length = 100

        repos.generations.add_record(
            generation_id,
            object_id,
            chunk_id,
            record_offset=offset,
            record_length=record_length,
        )

        offset += record_length

    repos.generations.update_status(generation_id, "SEALING")

    for index in range(data_shards):
        repos.shards.put(
            generation_id,
            index,
            {
                "path": f"generations/{generation_id}/shard_{index}.bin",
                "size": 100,
                "checksum": f"test-checksum-{generation_id}-{index}",
            },
        )

    repos.generations.update_status(generation_id, "STORED")
    repos.generations.update_status(generation_id, "AVAILABLE")
