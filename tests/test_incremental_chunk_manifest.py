from __future__ import annotations

import hashlib
import json

import pytest

from engram.chunking import FixedSizeChunker
from engram.incremental.chunk_manifest import ChunkManifestStore
from engram.encryption import generate_key


def test_chunk_manifest_round_trip_and_content_dedup(tmp_path):
    catalog = ChunkManifestStore(tmp_path, FixedSizeChunker(4))
    key = generate_key()
    first = b"AAAABBBBCCCC"
    second = b"ZZZZBBBBCCCC"

    meta1 = catalog.put_version("doc", 1, first, key, parent_version=None)
    meta2 = catalog.put_version("doc", 2, second, key, parent_version=1)

    assert catalog.get_version("doc", 1, key) == first
    assert catalog.get_version("doc", 2, key) == second
    assert catalog.verify_version("doc", 1, key)
    assert catalog.verify_version("doc", 2, key)
    assert meta1["chunk_count"] == 3
    assert meta2["chunk_count"] == 3
    assert meta1["chunk_manifest_root"] != meta2["chunk_manifest_root"]

    # BBBB and CCCC are stored once each across both versions.
    assert catalog.unique_chunk_count("doc") == 4


def test_chunk_payloads_are_encrypted_at_rest(tmp_path):
    catalog = ChunkManifestStore(tmp_path, FixedSizeChunker(4))
    key = generate_key()
    secret = b"SECRET-DATA"
    catalog.put_version("private", 1, secret, key, parent_version=None)

    chunk_files = list((tmp_path / "chunks").rglob("*.enc"))
    assert chunk_files
    assert all(secret not in path.read_bytes() for path in chunk_files)
    assert catalog.get_version("private", 1, key) == secret


def test_manifest_tampering_is_detected(tmp_path):
    catalog = ChunkManifestStore(tmp_path, FixedSizeChunker(4))
    key = generate_key()
    catalog.put_version("doc", 1, b"abcdefgh", key, parent_version=None)
    path = catalog._manifest_path("doc", 1)
    manifest = json.loads(path.read_text())
    manifest["chunks"][0]["sha256"] = hashlib.sha256(b"evil").hexdigest()
    path.write_text(json.dumps(manifest))
    assert not catalog.verify_version("doc", 1, key)


def test_wrong_key_fails_authentication(tmp_path):
    catalog = ChunkManifestStore(tmp_path, FixedSizeChunker(4))
    key = generate_key()
    catalog.put_version("doc", 1, b"abcdefgh", key, parent_version=None)
    with pytest.raises(Exception):
        catalog.get_version("doc", 1, generate_key())


def test_empty_version_has_valid_empty_manifest(tmp_path):
    catalog = ChunkManifestStore(tmp_path, FixedSizeChunker(4))
    key = generate_key()
    meta = catalog.put_version("doc", 1, b"", key, parent_version=None)
    assert meta["chunk_count"] == 0
    assert catalog.get_version("doc", 1, key) == b""
    assert catalog.verify_version("doc", 1, key)


def test_incremental_store_commits_chunk_manifest_into_mmr(tmp_path):
    from engram.incremental import IncrementalStore

    store = IncrementalStore(
        tmp_path,
        data_shards=2,
        total_shards=4,
        chunk_size=4,
        size_threshold=1024,
        max_wait_seconds=3600,
        chunker=FixedSizeChunker(4),
    )
    store.append("doc", b"AAAABBBB")
    store.seal("doc")
    store.insert("doc", 4, b"BBBB")
    store.seal("doc")

    assert store.get_version("doc", 1) == b"AAAABBBB"
    assert store.get_version("doc", 2) == b"AAAABBBBBBBB"
    assert store.get_version_from_chunks("doc", 1) == b"AAAABBBB"
    assert store.get_version_from_chunks("doc", 2) == b"AAAABBBBBBBB"
    assert store.verify_history("doc")

    state = store._load("doc")
    manifests = state["mmr"]["manifests"]
    assert manifests[0]["schema_version"] == 2
    assert manifests[1]["schema_version"] == 2
    assert manifests[0]["chunk_manifest_root"]
    assert manifests[1]["chunk_manifest_root"]
    assert store.get_chunk_storage_stats("doc", 2)["unique_chunk_count"] == 2
