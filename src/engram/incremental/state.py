#engram/incremental/state.py
"""Persistence helpers for long-lived object metadata and chunk commitments."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from engram.encryption import generate_key


class StateMixin:
    # Version 2 moves open-buffer bytes and staged ciphertext out of JSON metadata.
    STATE_VERSION = 2

    @staticmethod
    def _object_key(object_id: str) -> str:
        if not isinstance(object_id, str) or not object_id.strip():
            raise ValueError("object_id must be a non-empty string")
        return hashlib.sha256(object_id.encode("utf-8")).hexdigest()[:24]

    def _state_path(self, object_id: str) -> Path:
        return self.state_dir / f"{self._object_key(object_id)}.json"

    @staticmethod
    def _atomic_write(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "wb", dir=path.parent, prefix=f".{path.name}.", delete=False
            ) as f:
                temp_name = f.name
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_name, path)
        finally:
            if temp_name and os.path.exists(temp_name):
                os.unlink(temp_name)

    def _save(self, object_id: str, state: dict[str, Any]) -> None:
        state["object_id"] = object_id
        state["schema_version"] = self.STATE_VERSION
        raw = json.dumps(state, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self._atomic_write(self._state_path(object_id), raw)

    def _load(self, object_id: str, *, create: bool = False) -> dict[str, Any]:
        path = self._state_path(object_id)
        if not path.exists():
            if not create:
                raise KeyError(f"Unknown object: {object_id}")
            state = {
                "schema_version": self.STATE_VERSION,
                "object_id": object_id,
                # Simulation convenience only: production systems need KMS/secret storage.
                "key_b64": self._b64(generate_key()),
                "next_version": 1,
                "versions": [],
                "generations": [],
                "open": {"started_at": None, "buffer_size": 0, "entries": []},
                "sealing": None,
            }
            self._save(object_id, state)
            return state

        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Cannot read state for object {object_id!r}") from exc

        if state.get("object_id") != object_id:
            raise ValueError("Object state schema or object_id mismatch")

        # One-time migration for state files created by the original MVP, where
        # both the open buffer and staged ciphertext were embedded as Base64 JSON.
        if state.get("schema_version") == 1:
            opened = state.setdefault("open", {"started_at": None, "entries": []})
            legacy_buffer = self._unb64(opened.pop("buffer_b64", ""))
            opened["buffer_size"] = len(legacy_buffer)
            if legacy_buffer:
                self._atomic_write(self._buffer_path(object_id), legacy_buffer)

            staged = state.get("sealing")
            if staged and "ciphertext_b64" in staged:
                legacy_ciphertext = self._unb64(staged.pop("ciphertext_b64"))
                self._atomic_write(self._staging_payload_path(object_id), legacy_ciphertext)
            self._save(object_id, state)

        if state.get("schema_version") != self.STATE_VERSION:
            raise ValueError("Unsupported object state schema version")
        if "buffer_b64" in state.get("open", {}):
            raise ValueError("Legacy buffer field remains after state migration")
        return state

    @staticmethod
    def _b64(data: bytes) -> str:
        return base64.b64encode(data).decode("ascii")

    @staticmethod
    def _unb64(value: str) -> bytes:
        return base64.b64decode(value.encode("ascii"), validate=True)

    @staticmethod
    def _chunk_root(data: bytes, chunk_size: int) -> str:
        """Root-like deterministic commitment over fixed-size chunks (SHA-256 MVP)."""
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        digests = [
            hashlib.sha256(data[offset:offset + chunk_size]).digest()
            for offset in range(0, len(data), chunk_size)
        ]
        if not digests:
            digests.append(hashlib.sha256(b"").digest())
        level = digests
        while len(level) > 1:
            next_level = []
            for i in range(0, len(level), 2):
                right = level[i + 1] if i + 1 < len(level) else level[i]
                next_level.append(
                    hashlib.sha256(b"ENGRAM_CHUNK_NODE_V1" + level[i] + right).digest()
                )
            level = next_level
        return level[0].hex()

    def _chunk_layout(self, data: bytes) -> list[int]:
        return [
            len(data[i:i + self.chunk_size])
            for i in range(0, len(data), self.chunk_size)
        ]
