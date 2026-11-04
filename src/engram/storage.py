#engram/storage.py
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from engram.erasure_coding import EncodedShards
from engram.merkle import (
    CHUNK_BYTES,
    MerkleProof,
    MerkleTree,
    build_chunk_merkle_tree,
    build_hash_tree,
)


@dataclass(frozen=True)
class ChunkCommitment:
    shard_roots: tuple[int, ...]
    global_tree: MerkleTree


@dataclass(frozen=True)
class ChunkMetadata:
    chunk_id: str
    original_size: int
    data_shards: int
    total_shards: int


class StorageManager:
    """Simulate distributed storage nodes and chunk manifests.

    Each shard is stored on a separate node.

    Merkle layout:
        chunk bytes -> chunk Merkle tree -> shard root
        shard roots -> global Merkle tree -> global root

    Only shard roots and the small global tree are persisted in the manifest.
    Per-shard chunk trees are rebuilt when a challenge requests a chunk.
    """

    def __init__(self, root_dir: str | Path, node_count: int):
        if not isinstance(node_count, int) or isinstance(node_count, bool):
            raise TypeError("node_count must be an integer")
        if node_count <= 0:
            raise ValueError("node_count must be positive")

        self.root_dir = Path(root_dir)
        self.nodes_dir = self.root_dir / "nodes"
        self.manifests_dir = self.root_dir / "manifests"
        self.node_count = node_count

        self.nodes_dir.mkdir(parents=True, exist_ok=True)
        self.manifests_dir.mkdir(parents=True, exist_ok=True)

        self.node_dirs = [
            self.nodes_dir / f"node_{i}"
            for i in range(node_count)
        ]
        for node_dir in self.node_dirs:
            node_dir.mkdir(parents=True, exist_ok=True)

        self.node_available = [True] * node_count
        self.bytes_written = 0

    @staticmethod
    def _validate_chunk_id(chunk_id: str) -> str:
        if not isinstance(chunk_id, str) or not chunk_id:
            raise ValueError("chunk_id must be a non-empty string")

        # Keep IDs safe for filenames.
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", chunk_id):
            raise ValueError(
                "chunk_id may contain only letters, digits, '_', '-', and '.'"
            )
        if chunk_id in {".", ".."}:
            raise ValueError("Invalid chunk_id")

        return chunk_id

    def _manifest_path(self, chunk_id: str) -> Path:
        chunk_id = self._validate_chunk_id(chunk_id)
        return self.manifests_dir / f"{chunk_id}.json"

    def _shard_path(self, chunk_id: str, shard_index: int) -> Path:
        chunk_id = self._validate_chunk_id(chunk_id)
        if not 0 <= shard_index < self.node_count:
            raise IndexError(f"Invalid shard index: {shard_index}")

        return (
            self.node_dirs[shard_index]
            / f"{chunk_id}_shard_{shard_index}.bin"
        )

    @staticmethod
    def _sha256(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def _encode_hash(value: int) -> str:
        return hex(value)

    @staticmethod
    def _decode_hash(value: str) -> int:
        # Hash values are persisted as strings such as "0x1234".
        return int(value, 16)

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        """Write a file through a temporary file before replacing the target."""
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None

        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as f:
                temp_path = Path(f.name)
                f.write(data)
                f.flush()
                os.fsync(f.fileno())

            os.replace(temp_path, path)
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()

    def _read_manifest(self, chunk_id: str) -> dict[str, Any]:
        path = self._manifest_path(chunk_id)
        if not path.is_file():
            raise FileNotFoundError(f"Manifest not found for chunk {chunk_id!r}")

        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"Cannot read manifest for chunk {chunk_id!r}"
            ) from exc

        if manifest.get("chunk_id") != chunk_id:
            raise ValueError("Manifest chunk_id does not match requested ID")

        return manifest

    @staticmethod
    def _tree_from_manifest(manifest: dict[str, Any]) -> MerkleTree:
        """Reconstruct the small global tree from persisted levels."""
        raw_levels = manifest.get("merkle_levels")
        if not isinstance(raw_levels, list) or not raw_levels:
            raise ValueError("Manifest is missing global Merkle tree levels")

        levels: list[tuple[int, ...]] = []
        for raw_level in raw_levels:
            if not isinstance(raw_level, list) or not raw_level:
                raise ValueError("Invalid global Merkle tree level")
            levels.append(
                tuple(StorageManager._decode_hash(value) for value in raw_level)
            )

        return MerkleTree(levels=tuple(levels))

    
    def _validate_shards(
        self,
        chunk_id: str,
        shards: list[bytes],
    ) -> None:
        """Validate chunk ID and encoded shards."""
        self._validate_chunk_id(chunk_id)

        if not shards:
            raise ValueError("Cannot store an empty shard collection")

        if len(shards) != self.node_count:
            raise ValueError(
                f"Expected {self.node_count} shards, got {len(shards)}"
            )

        if any(not isinstance(shard, bytes) or not shard for shard in shards):
            raise ValueError("Every shard must be non-empty bytes")

        if self._manifest_path(chunk_id).exists():
            raise FileExistsError(
                f"Chunk {chunk_id!r} already exists; delete it before replacing"
            )

        for index in range(len(shards)):
            path = self._shard_path(chunk_id, index)
            if path.exists():
                raise FileExistsError(f"Shard file already exists: {path}")

    def build_commitment(
        self,
        shards: list[bytes],
    ) -> ChunkCommitment:
        """Build per-shard and global Merkle commitments.

        This method does not perform any filesystem writes.
        """
        if not shards:
            raise ValueError("Cannot build commitment for empty shards")

        shard_trees = [
            build_chunk_merkle_tree(shard)
            for shard in shards
        ]

        shard_roots = tuple(tree.root for tree in shard_trees)

        global_tree = build_hash_tree(list(shard_roots))

        return ChunkCommitment(
            shard_roots=shard_roots,
            global_tree=global_tree,
        )

    def write_shards(
        self,
        chunk_id: str,
        shards: list[bytes],
    ) -> list[dict[str, Any]]:
        """Write encoded shard files without generating commitments.

        This method does not calculate SHA-256 hashes or write manifests.
        """
        chunk_id = self._validate_chunk_id(chunk_id)

        if len(shards) != self.node_count:
            raise ValueError(
                f"Expected {self.node_count} shards, got {len(shards)}"
            )

        records: list[dict[str, Any]] = []

        for index, shard in enumerate(shards):
            if not isinstance(shard, bytes) or not shard:
                raise ValueError(
                    f"Shard {index} must be non-empty bytes"
                )

            shard_path = self._shard_path(chunk_id, index)

            if shard_path.exists():
                raise FileExistsError(
                    f"Shard file already exists: {shard_path}"
                )

            self._atomic_write(shard_path, shard)
            self.bytes_written += len(shard)

            records.append({
                "index": index,
                "size": len(shard),
                "file": shard_path.name,
            })

        return records

    def build_manifest(
        self,
        chunk_id: str,
        encoded: EncodedShards,
        shards: list[bytes],
        shard_records: list[dict[str, Any]],
        commitment: ChunkCommitment,
    ) -> dict[str, Any]:
        """Build the manifest object in memory.

        SHA-256 integrity hashes are calculated here, separately from
        shard file writing and Merkle commitment generation.
        """
        chunk_id = self._validate_chunk_id(chunk_id)

        if len(shards) != len(shard_records):
            raise ValueError("Shard records do not match shard collection")

        if len(shards) != len(commitment.shard_roots):
            raise ValueError("Commitment does not match shard collection")

        manifest_shards: list[dict[str, Any]] = []

        for shard, record in zip(shards, shard_records):
            manifest_shards.append({
                **record,
                "sha256": self._sha256(shard),
            })

        return {
            "chunk_id": chunk_id,
            "original_size": int(encoded.original_size),
            "data_shards": int(encoded.data_shards),
            "total_shards": len(shards),
            "shards": manifest_shards,
            "shard_roots": [
                self._encode_hash(root)
                for root in commitment.shard_roots
            ],
            "merkle_root": self._encode_hash(
                commitment.global_tree.root
            ),
            "merkle_levels": [
                [self._encode_hash(value) for value in level]
                for level in commitment.global_tree.levels
            ],
        }

    def persist_manifest(
        self,
        chunk_id: str,
        manifest: dict[str, Any],
    ) -> None:
        """Serialize and atomically persist a completed manifest."""
        chunk_id = self._validate_chunk_id(chunk_id)
        manifest_path = self._manifest_path(chunk_id)

        if manifest.get("chunk_id") != chunk_id:
            raise ValueError("Manifest chunk_id does not match requested ID")

        if manifest_path.exists():
            raise FileExistsError(
                f"Chunk {chunk_id!r} already exists; delete it before replacing"
            )

        self._atomic_write(
            manifest_path,
            json.dumps(
                manifest,
                indent=2,
                ensure_ascii=False,
            ).encode("utf-8"),
        )

    def store_chunk(
        self,
        chunk_id: str,
        encoded: EncodedShards,
    ) -> ChunkMetadata:
        """Execute the complete chunk storage workflow.

        Preserves the original public API and operation order.
        """
        chunk_id = self._validate_chunk_id(chunk_id)
        shards = list(encoded.shards)

        self._validate_shards(chunk_id, shards)

        # 1. Generate Merkle commitments.
        commitment = self.build_commitment(shards)

        # 2. Write shard files.
        shard_records = self.write_shards(chunk_id, shards)

        # 3. Build manifest metadata.
        manifest = self.build_manifest(
            chunk_id=chunk_id,
            encoded=encoded,
            shards=shards,
            shard_records=shard_records,
            commitment=commitment,
        )

        # 4. Publish manifest last.
        self.persist_manifest(chunk_id, manifest)

        return ChunkMetadata(
            chunk_id=chunk_id,
            original_size=int(encoded.original_size),
            data_shards=int(encoded.data_shards),
            total_shards=len(shards),
        )


    def delete_chunk(self, chunk_id: str) -> int:
        """Delete a chunk and return the number of shard bytes removed."""
        chunk_id = self._validate_chunk_id(chunk_id)
        manifest_path = self._manifest_path(chunk_id)
        manifest = self._read_manifest(chunk_id)

        deleted_bytes = 0
        for record in manifest.get("shards", []):
            index = int(record["index"])
            shard_path = self._shard_path(chunk_id, index)
            if shard_path.exists():
                deleted_bytes += shard_path.stat().st_size
                shard_path.unlink()

        if manifest_path.exists():
            manifest_path.unlink()

        return deleted_bytes

    def get_chunk_metadata(self, chunk_id: str) -> ChunkMetadata:
        manifest = self._read_manifest(chunk_id)
        return ChunkMetadata(
            chunk_id=chunk_id,
            original_size=int(manifest["original_size"]),
            data_shards=int(manifest["data_shards"]),
            total_shards=int(manifest["total_shards"]),
        )

    def set_node_available(self, node_index: int, available: bool) -> None:
        if not 0 <= node_index < self.node_count:
            raise IndexError(f"Invalid node index: {node_index}")
        if not isinstance(available, bool):
            raise TypeError("available must be a bool")

        self.node_available[node_index] = available

    def load_available_shards(self, chunk_id: str) -> dict[int, bytes]:
        """Read all currently available, intact shards for a chunk."""
        manifest = self._read_manifest(chunk_id)
        available: dict[int, bytes] = {}

        for record in manifest["shards"]:
            index = int(record["index"])
            if not self.node_available[index]:
                continue

            path = self._shard_path(chunk_id, index)
            if not path.is_file():
                continue

            data = path.read_bytes()
            if len(data) != int(record["size"]):
                continue
            if self._sha256(data) != record["sha256"]:
                continue

            available[index] = data

        return available

    def get_stored_bytes(self) -> int:
        """Return the total bytes currently stored in node shard files."""
        total = 0
        for node_dir in self.node_dirs:
            for path in node_dir.glob("*.bin"):
                if path.is_file():
                    total += path.stat().st_size
        return total

    def get_merkle_root(self, chunk_id: str) -> int:
        manifest = self._read_manifest(chunk_id)
        return self._decode_hash(manifest["merkle_root"])

    def get_chunk_counts(self, chunk_id: str) -> tuple[int, ...]:
        """Return the number of 31-byte chunks in each stored shard."""
        manifest = self._read_manifest(chunk_id)
        counts: list[int] = []

        for record in manifest["shards"]:
            size = int(record["size"])
            count = (size + CHUNK_BYTES - 1) // CHUNK_BYTES
            if count <= 0:
                raise ValueError(
                    f"Shard {record['index']} has no chunks"
                )
            counts.append(count)

        return tuple(counts)

    def get_chunk_response(
        self,
        chunk_id: str,
        shard_index: int,
        chunk_index: int,
    ) -> tuple[bytes, MerkleProof, MerkleProof]:
        """Return chunk bytes, its chunk path, and its global shard path.

        The chunk Merkle tree is rebuilt from the requested shard on demand.
        No per-shard chunk-tree levels are persisted in the manifest.
        """
        manifest = self._read_manifest(chunk_id)
        total_shards = int(manifest["total_shards"])

        if not 0 <= shard_index < total_shards:
            raise IndexError(f"Invalid shard index: {shard_index}")
        if not self.node_available[shard_index]:
            raise ConnectionError(f"Storage node {shard_index} is unavailable")

        shard_path = self._shard_path(chunk_id, shard_index)
        if not shard_path.is_file():
            raise FileNotFoundError(
                f"Shard {shard_index} for chunk {chunk_id!r} is missing"
            )

        shard_data = shard_path.read_bytes()
        record = manifest["shards"][shard_index]

        # Check the stored shard before deriving any proof from it.
        if len(shard_data) != int(record["size"]):
            raise ValueError("Stored shard size does not match manifest")
        if self._sha256(shard_data) != record["sha256"]:
            raise ValueError("Stored shard hash does not match manifest")

        # Rebuild the requested shard's chunk tree.
        chunk_tree = build_chunk_merkle_tree(shard_data)
        stored_shard_root = self._decode_hash(
            manifest["shard_roots"][shard_index]
        )
        if chunk_tree.root != stored_shard_root:
            raise ValueError(
                "Rebuilt chunk tree root does not match stored shard root"
            )

        if not 0 <= chunk_index < chunk_tree.leaf_count:
            raise IndexError(
                f"Invalid chunk index {chunk_index}; "
                f"shard has {chunk_tree.leaf_count} chunks"
            )

        start = chunk_index * CHUNK_BYTES
        chunk_data = shard_data[start:start + CHUNK_BYTES]

        chunk_proof = chunk_tree.generate_proof(chunk_index)

        # The global tree is small (one leaf per shard); reconstruct it from
        # the stored levels to obtain the selected shard's inclusion path.
        global_tree = self._tree_from_manifest(manifest)
        expected_global_root = self._decode_hash(manifest["merkle_root"])
        if global_tree.root != expected_global_root:
            raise ValueError("Global Merkle root does not match manifest")

        shard_proof = global_tree.generate_proof(shard_index)

        return chunk_data, chunk_proof, shard_proof

    def get_shard_layout(
        self,
        chunk_id: str,
    ) -> tuple[int, int]:
        """Return (shard_bytes_len, total_shards).
    
        All shards must have equal size.
        """
    
        manifest = self._read_manifest(chunk_id)
    
        sizes = [
            int(record["size"])
            for record in manifest["shards"]
        ]
    
        if not sizes:
            raise ValueError("Manifest contains no shards")
    
        if len(set(sizes)) != 1:
            raise ValueError(
                "All shards must have the same size"
            )
    
        return sizes[0], int(manifest["total_shards"])