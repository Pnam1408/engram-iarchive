
import pytest

from engram.erasure_coding import encode, decode
from engram.merkle import build_merkle_tree
from engram.storage import StorageManager


def test_store_and_load_chunk(tmp_path):
    storage = StorageManager(tmp_path, node_count=6)
    original = b"Engram storage test"

    encoded = encode(original, data_shards=4, total_shards=6)
    metadata = storage.store_chunk("chunk_001", encoded)

    assert metadata.chunk_id == "chunk_001"
    assert storage.get_stored_bytes() == sum(
        len(shard) for shard in encoded.shards
    )

    available = storage.load_available_shards("chunk_001")

    restored = decode(
        available,
        original_size=metadata.original_size,
        data_shards=metadata.data_shards,
        total_shards=metadata.total_shards,
    )

    assert restored == original


def test_recover_when_two_nodes_are_unavailable(tmp_path):
    storage = StorageManager(tmp_path, node_count=6)
    original = b"Recover data after node failure"

    encoded = encode(original, data_shards=4, total_shards=6)
    storage.store_chunk("chunk_002", encoded)

    storage.set_node_available(1, False)
    storage.set_node_available(4, False)

    available = storage.load_available_shards("chunk_002")

    assert len(available) == 4

    restored = decode(
        available,
        original_size=len(original),
        data_shards=4,
        total_shards=6,
    )

    assert restored == original


def test_tampered_shard_is_rejected(tmp_path):
    storage = StorageManager(tmp_path, node_count=6)
    encoded = encode(b"Integrity test", data_shards=4, total_shards=6)

    storage.store_chunk("chunk_003", encoded)

    shard_path = tmp_path / "nodes" / "node_0" / "chunk_003_shard_0.bin"
    shard_path.write_bytes(b"tampered data")

    available = storage.load_available_shards("chunk_003")

    assert 0 not in available
    assert len(available) == 5


def test_duplicate_chunk_is_rejected(tmp_path):
    storage = StorageManager(tmp_path, node_count=6)
    encoded = encode(b"Test", data_shards=4, total_shards=6)

    storage.store_chunk("chunk_004", encoded)

    with pytest.raises(FileExistsError):
        storage.store_chunk("chunk_004", encoded)


def test_missing_chunk_raises_error(tmp_path):
    storage = StorageManager(tmp_path, node_count=6)

    with pytest.raises(FileNotFoundError):
        storage.load_available_shards("unknown_chunk")


def test_store_with_precomputed_merkle_tree(tmp_path):
    storage = StorageManager(tmp_path, node_count=6)
    encoded = encode(b"Precomputed tree", data_shards=4, total_shards=6)
    tree = build_merkle_tree(list(encoded.shards))

    storage.store_chunk("chunk_precomputed", encoded, precomputed_tree=tree)

    assert storage.get_merkle_root("chunk_precomputed") == tree.root


def test_delete_chunk_removes_shards_and_manifest(tmp_path):
    storage = StorageManager(tmp_path, node_count=6)
    encoded = encode(b"Delete me", data_shards=4, total_shards=6)
    storage.store_chunk("chunk_delete", encoded)

    deleted = storage.delete_chunk("chunk_delete")

    assert deleted == sum(map(len, encoded.shards))
    assert storage.get_stored_bytes() == 0
    with pytest.raises(FileNotFoundError):
        storage.get_chunk_metadata("chunk_delete")
