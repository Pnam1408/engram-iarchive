#engram/incremental/store.py
"""Public incremental store API and append orchestration."""
from __future__ import annotations

import hashlib
import time
from pathlib import Path

from engram.storage import StorageManager
from .buffer import BufferMixin
from .generations import GenerationMixin
from .versions import VersionMixin
from .types import VersionEntry

class IncrementalStore(BufferMixin, GenerationMixin, VersionMixin):
    """Append-only object store with per-object generation batching.

    A version is assigned on Append and stays PENDING until its generation is
    sealed, published, and successfully reconstructed from stored shards.
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
        self.storage = StorageManager(self.root_dir / "shard_storage", node_count=total_shards)
        self.data_shards = data_shards
        self.total_shards = total_shards
        self.chunk_size = chunk_size
        self.size_threshold = size_threshold
        self.max_wait_seconds = float(max_wait_seconds)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.buffer_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir.mkdir(parents=True, exist_ok=True)

    def append(self, object_id: str, delta: bytes) -> VersionEntry:
        """Append bytes to an object and assign a monotonically increasing version.

        The version remains PENDING until its generation has been stored and at
        least ``data_shards`` valid shards can be read back.
        """
        if not isinstance(delta, bytes):
            raise TypeError("delta must be bytes")
        if not delta:
            raise ValueError("delta must not be empty")

        state = self._load(object_id, create=True)
        self.recover(object_id)
        state = self._load(object_id)

        # If an open generation has aged out, seal it before accepting this delta.
        opened = state["open"]
        if (
            opened["entries"]
            and opened["started_at"] is not None
            and time.time() - opened["started_at"] >= self.max_wait_seconds
        ):
            self._seal(object_id, state)
            state = self._load(object_id)
            opened = state["open"]

        version = int(state["next_version"])
        entry: VersionEntry = {
            "version": version,
            "delta_size_bytes": len(delta),
            "delta_sha256": hashlib.sha256(delta).hexdigest(),
            "generation_id": None,
            "status": "PENDING",
        }
        opened = state["open"]
        if not opened["entries"]:
            opened["started_at"] = time.time()
        buffer = self._read_buffer(object_id, state) + delta
        # Persist bytes first, then publish their acknowledged length in JSON.
        # If a crash occurs between these writes, _read_buffer truncates the
        # uncommitted tail to the previous metadata length on recovery.
        self._write_buffer(object_id, buffer)
        opened["buffer_size"] = len(buffer)
        opened["entries"].append(entry)
        state["versions"].append(entry.copy())
        state["next_version"] = version + 1
        self._save(object_id, state)

        # Seal as soon as the threshold is reached; a large append is one batch.
        if len(buffer) >= self.size_threshold:
            state = self._load(object_id)
            self._seal(object_id, state)

        versions = self.list_versions(object_id)
        return next(v for v in versions if v["version"] == version)
