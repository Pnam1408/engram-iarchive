# engram/incremental/state.py
"""Persistence helpers for long-lived object metadata and chunk commitments."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import cast

from engram.encryption import generate_key
from engram.mmr import MMRAccumulator

from .contracts import BufferAccess, StateAccess, GenerationAccess
from .types import (
    GenerationRecord,
    LegacyOpenBuffer,
    LegacyStagedPayload,
    ObjectState,
    VersionEntry,
)
from .manifest import build_version_manifest


class StateMixin(StateAccess, BufferAccess, GenerationAccess):

    STATE_VERSION = 4

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

    def _save(self, object_id: str, state: ObjectState) -> None:
        state["object_id"] = object_id
        state["schema_version"] = self.STATE_VERSION
        raw = json.dumps(state, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self._atomic_write(self._state_path(object_id), raw)

    def _load(
        self,
        object_id: str,
        *,
        create: bool = False,
    ) -> ObjectState:
        path = self._state_path(object_id)

        if not path.exists():
            if not create:
                raise KeyError(f"Unknown object: {object_id}")

            state = self._create_initial_state(object_id)
            self._save(object_id, state)
            return state

        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Cannot read state for object {object_id!r}") from exc

        if state.get("object_id") != object_id:
            raise ValueError("Object state object_id mismatch")

        schema_version = state.get("schema_version")
        if schema_version == 3:
            # Migrate append-only metadata without changing existing MMR leaves.
            for record in state.get("versions", []):
                record.setdefault("operation", "append")
                record.setdefault("offset", None)
                record.setdefault("delete_length", 0)
                record.setdefault("storage_size_bytes", int(record["delta_size_bytes"]))
            collections = [
                state.get("generations", []),
                [state.get("sealing")],
                [{"entries": state.get("open", {}).get("entries", [])}],
            ]
            for collection in collections:
                for item in collection:
                    if not item:
                        continue
                    for record in item.get("entries", []):
                        record.setdefault("operation", "append")
                        record.setdefault("offset", None)
                        record.setdefault("delete_length", 0)
                        record.setdefault(
                            "storage_size_bytes", int(record["delta_size_bytes"])
                        )
            state["schema_version"] = self.STATE_VERSION
            self._save(object_id, cast(ObjectState, state))
        elif schema_version != self.STATE_VERSION:
            raise ValueError(
                f"Unsupported schema version: {schema_version}; "
                f"expected {self.STATE_VERSION}"
            )

        if "mmr" not in state:
            raise ValueError("Object state is missing its MMR")

        MMRAccumulator.from_dict(state["mmr"])

        return cast(ObjectState, state)

    def _create_initial_state(self, object_id: str) -> ObjectState:
        """Create a new object state with no versions or generations."""
        return {
            "schema_version": self.STATE_VERSION,
            "object_id": object_id,
            # Simulation convenience only: production systems need KMS/secret storage.
            "key_b64": self._b64(generate_key()),
            "next_version": 1,
            "versions": [],
            "generations": [],
            "open": {"started_at": None, "buffer_size": 0, "entries": []},
            "sealing": None,
            "mmr": MMRAccumulator().to_dict(),
        }

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
            hashlib.sha256(data[offset : offset + chunk_size]).digest()
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
            len(data[i : i + self.chunk_size])
            for i in range(0, len(data), self.chunk_size)
        ]
