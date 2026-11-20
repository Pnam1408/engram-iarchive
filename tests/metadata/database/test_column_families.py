from engram.metadata.database.column_families import COLUMN_FAMILIES, ColumnFamily


def test_physical_column_families_are_registered():
    expected = {
        "generations",
        "generation_manifests",
        "generation_manifest_entries",
        "generation_shards",
        "chunk_placements",
        "chunk_placement_index",
        "shard_placements",
        "shard_placement_index",
        "storage_receipts",
        "storage_receipt_index",
    }

    assert expected.issubset(set(COLUMN_FAMILIES))
    assert all(value in COLUMN_FAMILIES for value in expected)


def test_legacy_physical_column_families_are_preserved():
    assert ColumnFamily.GENERATION_CHUNKS.value in COLUMN_FAMILIES
    assert ColumnFamily.SHARDS.value in COLUMN_FAMILIES
