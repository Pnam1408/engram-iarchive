#tests/test_storage.py
import json

import pytest

from engram.erasure_coding import encode, decode
from engram.merkle import (
    build_chunk_merkle_tree,
    verify_hash_path,
)
from engram.storage import (
    ChunkCommitment,
    ChunkMetadata,
    StorageManager,
)


@pytest.fixture
def storage(tmp_path):
    return StorageManager(tmp_path, node_count=6)


@pytest.fixture
def sample_data():
    original = b"Engram storage test"
    encoded = encode(original, data_shards=4, total_shards=6)
    return original, encoded


def test_store_and_load_chunk(storage, sample_data):
    """The original high-level API should remain functional."""
    original, encoded = sample_data

    metadata = storage.store_chunk("chunk_001", encoded)

    assert isinstance(metadata, ChunkMetadata)
    assert metadata.chunk_id == "chunk_001"
    assert metadata.original_size == len(original)
    assert metadata.data_shards == 4
    assert metadata.total_shards == 6

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


def test_build_commitment_without_writing_files(storage, sample_data):
    """Commitment generation should not perform filesystem writes."""
    _, encoded = sample_data
    shards = list(encoded.shards)

    commitment = storage.build_commitment(shards)

    assert isinstance(commitment, ChunkCommitment)
    assert len(commitment.shard_roots) == 6
    assert commitment.global_tree.root > 0

    assert storage.get_stored_bytes() == 0
    assert list(storage.manifests_dir.glob("*.json")) == []


def test_write_shards_without_manifest(storage, sample_data):
    """Shard writing should not generate or persist a manifest."""
    _, encoded = sample_data
    shards = list(encoded.shards)

    records = storage.write_shards("chunk_write_only", shards)

    assert len(records) == 6
    assert storage.get_stored_bytes() == sum(map(len, shards))

    assert list(storage.manifests_dir.glob("*.json")) == []

    for index, (record, shard) in enumerate(zip(records, shards)):
        assert record["index"] == index
        assert record["size"] == len(shard)
        assert record["file"] == (
            f"chunk_write_only_shard_{index}.bin"
        )

        assert "sha256" not in record

        shard_path = (
            storage.nodes_dir
            / f"node_{index}"
            / record["file"]
        )
        assert shard_path.read_bytes() == shard


def test_build_manifest_without_persisting(storage, sample_data):
    """Manifest construction should remain an in-memory operation."""
    _, encoded = sample_data
    shards = list(encoded.shards)

    commitment = storage.build_commitment(shards)
    records = storage.write_shards("chunk_manifest", shards)

    manifest = storage.build_manifest(
        "chunk_manifest",
        encoded,
        shards,
        records,
        commitment,
    )

    assert manifest["chunk_id"] == "chunk_manifest"
    assert manifest["total_shards"] == 6
    assert len(manifest["shards"]) == 6
    assert len(manifest["shard_roots"]) == 6

    for index, (record, shard) in enumerate(
        zip(manifest["shards"], shards)
    ):
        assert record["index"] == index
        assert record["size"] == len(shard)
        assert record["sha256"] == storage._sha256(shard)

    assert not storage._manifest_path("chunk_manifest").exists()


def test_persist_manifest(storage, sample_data):
    """A completed manifest should be persisted and readable."""
    _, encoded = sample_data
    shards = list(encoded.shards)

    commitment = storage.build_commitment(shards)
    records = storage.write_shards("chunk_persist", shards)

    manifest = storage.build_manifest(
        "chunk_persist",
        encoded,
        shards,
        records,
        commitment,
    )

    storage.persist_manifest("chunk_persist", manifest)

    persisted = json.loads(
        storage._manifest_path("chunk_persist").read_text(
            encoding="utf-8"
        )
    )

    assert persisted == manifest
    assert storage.get_merkle_root("chunk_persist") == (
        commitment.global_tree.root
    )


def test_recover_when_two_nodes_are_unavailable(storage):
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


def test_tampered_shard_is_rejected(storage):
    encoded = encode(
        b"Integrity test",
        data_shards=4,
        total_shards=6,
    )

    storage.store_chunk("chunk_003", encoded)

    shard_path = (
        storage.nodes_dir
        / "node_0"
        / "chunk_003_shard_0.bin"
    )
    shard_path.write_bytes(b"tampered data")

    available = storage.load_available_shards("chunk_003")

    assert 0 not in available
    assert len(available) == 5


def test_duplicate_chunk_is_rejected(storage):
    encoded = encode(b"Test", data_shards=4, total_shards=6)

    storage.store_chunk("chunk_004", encoded)

    with pytest.raises(FileExistsError):
        storage.store_chunk("chunk_004", encoded)


def test_missing_chunk_raises_error(storage):
    with pytest.raises(FileNotFoundError):
        storage.load_available_shards("unknown_chunk")


def test_store_builds_chunk_level_merkle_commitment(storage):
    original = bytes(range(256)) * 2

    encoded = encode(
        original,
        data_shards=4,
        total_shards=6,
    )

    storage.store_chunk("chunk_commitment", encoded)

    assert storage.get_merkle_root("chunk_commitment") > 0
    assert len(storage.get_chunk_counts("chunk_commitment")) == 6

    shard_index = 2
    chunk_index = 0

    chunk, chunk_path, shard_path = storage.get_chunk_response(
        "chunk_commitment",
        shard_index,
        chunk_index,
    )

    shard = encoded.shards[shard_index]
    shard_tree = build_chunk_merkle_tree(shard)

    assert verify_hash_path(
        shard_tree.levels[0][chunk_index],
        chunk_path,
        shard_tree.root,
    )

    assert verify_hash_path(
        shard_tree.root,
        shard_path,
        storage.get_merkle_root("chunk_commitment"),
    )

    assert chunk == shard[:len(chunk)]


def test_delete_chunk_removes_shards_and_manifest(storage):
    encoded = encode(
        b"Delete me",
        data_shards=4,
        total_shards=6,
    )

    storage.store_chunk("chunk_delete", encoded)

    deleted = storage.delete_chunk("chunk_delete")

    assert deleted == sum(map(len, encoded.shards))
    assert storage.get_stored_bytes() == 0

    with pytest.raises(FileNotFoundError):
        storage.get_chunk_metadata("chunk_delete")


def test_write_shards_rejects_duplicate_shard_file(storage, sample_data):
    _, encoded = sample_data

    storage.write_shards(
        "chunk_partial",
        list(encoded.shards),
    )

    with pytest.raises(FileExistsError):
        storage.write_shards(
            "chunk_partial",
            list(encoded.shards),
        )


def test_build_commitment_rejects_empty_shards(storage):
    with pytest.raises(ValueError):
        storage.build_commitment([])


def test_write_shards_rejects_wrong_shard_count(storage):
    with pytest.raises(ValueError):
        storage.write_shards("chunk_invalid", [b"only one shard"])


def test_build_manifest_rejects_mismatched_shard_count(
    storage,
    sample_data,
):
    _, encoded = sample_data
    shards = list(encoded.shards)

    commitment = storage.build_commitment(shards)
    records = storage.write_shards("chunk_mismatch", shards)

    with pytest.raises(ValueError):
        storage.build_manifest(
            "chunk_mismatch",
            encoded,
            shards[:-1],
            records,
            commitment,
        )
