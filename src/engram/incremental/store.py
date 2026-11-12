# engram/incremental/store.py
"""Public incremental store API and append orchestration."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from engram.storage import StorageManager
from .buffer import BufferMixin
from .generations import GenerationMixin
from .versions import VersionMixin
from .types import OperationType, VersionEntry
from engram.chunking import Chunker, FixedSizeChunker
from .chunk_manifest import ChunkManifestStore


class IncrementalStore(BufferMixin, GenerationMixin, VersionMixin):
    """Operation-versioned object store with per-object generation batching.

    Each append/insert/delete creates an immutable version. A version stays
    PENDING until its generation is sealed, published, and reconstructed.
    """

    def __init__(
        self,
        root_dir: str | Path,
        *,
        data_shards: int = 4,
        total_shards: int = 8,
        chunk_size: int = 4096,
        size_threshold: int = 64 * 1024,
        max_wait_seconds: float = 10.0,
        chunker: Chunker | None = None,
    ) -> None:
        if not (1 <= data_shards < total_shards <= 255):
            raise ValueError("Require 1 <= data_shards < total_shards <= 255")
        if chunk_size <= 0 or size_threshold <= 0:
            raise ValueError("chunk_size and size_threshold must be positive")
        if max_wait_seconds <= 0:
            raise ValueError("max_wait_seconds must be positive")

        self.root_dir = Path(root_dir)
        self.state_dir = self.root_dir / "objects"
        self.buffer_dir = self.root_dir / "buffers"
        self.staging_dir = self.root_dir / "staging"
        self.storage = StorageManager(
            self.root_dir / "shard_storage", node_count=total_shards
        )
        self.data_shards = data_shards
        self.total_shards = total_shards
        self.chunk_size = chunk_size
        self.chunker = chunker or FixedSizeChunker(chunk_size)
        self.chunk_catalog = ChunkManifestStore(
            self.root_dir / "chunk_catalog", self.chunker
        )
        self.size_threshold = size_threshold
        self.max_wait_seconds = float(max_wait_seconds)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.buffer_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir.mkdir(parents=True, exist_ok=True)

    def append(self, object_id: str, delta: bytes) -> VersionEntry:
        """Append bytes and create a new immutable object version."""
        return self._record_operation(object_id, "append", delta, None, 0)

    def insert(self, object_id: str, offset: int, data: bytes) -> VersionEntry:
        """Insert bytes at a byte offset in the latest object version."""
        self._validate_payload(data)
        self._validate_offset(offset)
        self._load(object_id, create=True)
        self.recover(object_id)
        latest = self._latest_available_bytes(object_id)
        if offset > len(latest):
            raise ValueError(f"offset must be between 0 and {len(latest)}")
        return self._record_operation(object_id, "insert", data, offset, 0)

    def delete(self, object_id: str, offset: int, length: int) -> VersionEntry:
        """Delete a non-empty byte range from the latest object version."""
        self._validate_offset(offset)
        if not isinstance(length, int) or isinstance(length, bool) or length <= 0:
            raise ValueError("length must be a positive integer")
        self._load(object_id, create=True)
        self.recover(object_id)
        latest = self._latest_available_bytes(object_id)
        if offset + length > len(latest):
            raise ValueError(
                f"delete range [{offset}, {offset + length}) exceeds object size {len(latest)}"
            )
        return self._record_operation(object_id, "delete", b"", offset, length)

    @staticmethod
    def _validate_payload(data: bytes) -> None:
        if not isinstance(data, bytes):
            raise TypeError("data must be bytes")
        if not data:
            raise ValueError("data must not be empty")

    @staticmethod
    def _validate_offset(offset: int) -> None:
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise ValueError("offset must be a non-negative integer")

    def _latest_available_bytes(self, object_id: str) -> bytes:
        """Replay committed history plus the unsealed tail without forcing a seal."""
        state = self._load(object_id)
        available = [
            int(record["version"])
            for record in state["versions"]
            if record.get("status") == "Available"
        ]
        current = bytearray(
            self.get_version(object_id, max(available)) if available else b""
        )
        open_entries = state["open"]["entries"]
        if not open_entries:
            return bytes(current)

        pending = self._read_buffer(object_id, state)
        cursor = 0
        for entry in open_entries:
            storage_size = int(
                entry.get("storage_size_bytes", entry["delta_size_bytes"])
            )
            stored = pending[cursor : cursor + storage_size]
            cursor += storage_size
            if len(stored) != storage_size:
                raise ValueError("Pending operation storage size mismatch")
            operation = entry.get("operation", "append")
            payload = b"" if operation == "delete" else stored
            if operation == "delete" and stored != b"\x00":
                raise ValueError("Pending delete marker mismatch")
            if len(payload) != int(entry["delta_size_bytes"]):
                raise ValueError("Pending operation payload size mismatch")
            if hashlib.sha256(payload).hexdigest() != entry["delta_sha256"]:
                raise ValueError("Pending operation payload hash mismatch")
            self._apply_operation(current, entry, payload)
        if cursor != len(pending):
            raise ValueError("Pending buffer contains unreferenced bytes")
        return bytes(current)

    def _record_operation(
        self,
        object_id: str,
        operation: OperationType,
        payload: bytes,
        offset: int | None,
        delete_length: int,
    ) -> VersionEntry:
        if operation not in {"append", "insert", "delete"}:
            raise ValueError(f"Unsupported operation: {operation}")
        if operation == "append":
            self._validate_payload(payload)
        elif operation == "insert":
            self._validate_payload(payload)
            if offset is None:
                raise ValueError("insert requires an offset")
        elif operation == "delete":
            if payload or offset is None or delete_length <= 0:
                raise ValueError("delete requires an offset and positive delete_length")

        state = self._load(object_id, create=True)
        self.recover(object_id)
        state = self._load(object_id)

        opened = state["open"]
        if (
            opened["entries"]
            and opened["started_at"] is not None
            and time.time() - opened["started_at"] >= self.max_wait_seconds
        ):
            self._seal(object_id, state)
            state = self._load(object_id)
            opened = state["open"]

        # A one-byte framing marker gives delete operations a durable generation
        # payload even though their logical operation payload is empty.
        stored_payload = payload if payload else b"\x00"
        version = int(state["next_version"])
        entry: VersionEntry = {
            "version": version,
            "operation": operation,
            "offset": offset,
            "delete_length": delete_length,
            "delta_size_bytes": len(payload),
            "delta_sha256": hashlib.sha256(payload).hexdigest(),
            "storage_size_bytes": len(stored_payload),
            "generation_id": None,
            "status": "PENDING",
        }
        if not opened["entries"]:
            opened["started_at"] = time.time()
        buffer = self._read_buffer(object_id, state) + stored_payload
        self._write_buffer(object_id, buffer)
        opened["buffer_size"] = len(buffer)
        opened["entries"].append(entry)
        state["versions"].append(entry.copy())
        state["next_version"] = version + 1
        self._save(object_id, state)

        if len(buffer) >= self.size_threshold:
            state = self._load(object_id)
            self._seal(object_id, state)

        versions = self.list_versions(object_id)
        return next(v for v in versions if v["version"] == version)
