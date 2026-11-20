import pytest

from engram.metadata.database.key_codec import KeyCodec


def test_generation_manifest_key_is_scoped_by_generation():
    assert KeyCodec.generation_manifest_key("g-1") == KeyCodec.encode_string("g-1")
    assert KeyCodec.generation_manifest_key("g-1") != KeyCodec.generation_manifest_key("g-2")


def test_generation_manifest_entry_keys_are_offset_ordered_and_prefix_scoped():
    prefix = KeyCodec.generation_manifest_entry_prefix("g-1")
    key_1 = KeyCodec.generation_manifest_entry_key("g-1", 1)
    key_2 = KeyCodec.generation_manifest_entry_key("g-1", 2)

    assert key_1.startswith(prefix)
    assert key_2.startswith(prefix)
    assert key_1 < key_2
    assert not key_1.startswith(KeyCodec.generation_manifest_entry_prefix("g-2"))


def test_generation_shard_keys_are_index_ordered():
    prefix = KeyCodec.generation_shard_prefix("g-1")
    key_0 = KeyCodec.generation_shard_key("g-1", 0)
    key_1 = KeyCodec.generation_shard_key("g-1", 1)

    assert key_0.startswith(prefix)
    assert key_1.startswith(prefix)
    assert key_0 < key_1


def test_chunk_placement_index_key_has_unambiguous_components():
    prefix = KeyCodec.chunk_placement_index_prefix("object", "chunk")
    key = KeyCodec.chunk_placement_index_key("object", "chunk", "placement")

    assert key.startswith(prefix)
    assert key != KeyCodec.chunk_placement_index_key("object", "chunk-2", "placement")
    assert key != KeyCodec.chunk_placement_index_key("object-2", "chunk", "placement")


def test_shard_placement_index_key_is_scoped_and_ordered_by_shard_index():
    prefix = KeyCodec.shard_placement_index_prefix("g-1", 3)
    key = KeyCodec.shard_placement_index_key("g-1", 3, "placement")

    assert key.startswith(prefix)
    assert KeyCodec.shard_placement_index_key("g-1", 3, "p-1") != KeyCodec.shard_placement_index_key("g-1", 4, "p-1")


def test_storage_receipt_index_is_ordered_by_issue_time_then_id():
    prefix = KeyCodec.storage_receipt_index_prefix("placement")
    earlier = KeyCodec.storage_receipt_index_key("placement", 10, "r-2")
    later = KeyCodec.storage_receipt_index_key("placement", 11, "r-1")
    tie_breaker_a = KeyCodec.storage_receipt_index_key("placement", 10, "r-1")
    tie_breaker_b = KeyCodec.storage_receipt_index_key("placement", 10, "r-2")

    assert earlier.startswith(prefix)
    assert earlier < later
    assert tie_breaker_a < tie_breaker_b


@pytest.mark.parametrize("bad_index", [-1, True, 1.5])
def test_numeric_key_components_reject_invalid_uint64(bad_index):
    with pytest.raises((TypeError, ValueError)):
        KeyCodec.generation_shard_key("g-1", bad_index)


def test_string_key_components_reject_empty_values():
    with pytest.raises(ValueError):
        KeyCodec.chunk_placement_index_key("object", "", "placement")


def test_uint64_boundary_is_supported_for_manifest_offset():
    key = KeyCodec.generation_manifest_entry_key("g-1", KeyCodec.UINT64_MAX)
    assert key.endswith(KeyCodec.encode_uint64(KeyCodec.UINT64_MAX))


def test_legacy_key_methods_remain_compatible():
    assert KeyCodec.generation_key("g-1") == KeyCodec.encode_string("g-1")
    assert KeyCodec.generation_chunk_key("g-1", 2) == (
        KeyCodec.encode_string("g-1") + KeyCodec.encode_uint64(2)
    )
    assert KeyCodec.shard_key("g-1", 2) == (
        KeyCodec.encode_string("g-1") + KeyCodec.encode_uint64(2)
    )
