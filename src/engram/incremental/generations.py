"""Generation batching, sealing, publication, and crash recovery."""
from __future__ import annotations

import hashlib
import time
from typing import Any

from engram.encryption import EncryptedPayload, decrypt, encrypt
from engram.erasure_coding import decode, encode

class GenerationMixin:
    def _seal(self, object_id: str, state: dict[str, Any]) -> None:
        # A staged seal is resumed without needing the open buffer.
        if state.get("sealing") is not None:
            self._finish_seal(object_id, state)
            return

        opened = state["open"]
        if not opened["entries"]:
            return
        
        plaintext = self._read_buffer(object_id, state)
        entries = [dict(e) for e in opened["entries"]]
        start_version = int(entries[0]["version"])
        end_version = int(entries[-1]["version"])
        chunk_root = self._chunk_root(plaintext, self.chunk_size)
        object_hash = self._object_key(object_id)[:10]
        generation_id = f"g_{object_hash}_{start_version}_{end_version}_{chunk_root[:16]}"
        
        # Stage the encrypted payload durably before publishing shards. If the
        # process crashes, recover() can resume this exact sealing operation.
        if state.get("sealing") is None:
            key = self._unb64(state["key_b64"])
            encrypted = encrypt(plaintext, key, associated_data=generation_id.encode())
            state["sealing"] = {
                "generation_id": generation_id,
                "start_version": start_version,
                "end_version": end_version,
                "chunk_root": chunk_root,
                "plaintext_sha256": hashlib.sha256(plaintext).hexdigest(),
                "plaintext_size_bytes": len(plaintext),
                "nonce_b64": self._b64(encrypted.nonce),
                "ciphertext_size_bytes": len(encrypted.ciphertext),
                "entries": entries,
            }
            # Stage ciphertext in its own temporary file, not in the long-lived
            # metadata JSON, so large generations do not inflate state files.
            self._write_staging_payload(object_id, encrypted.ciphertext)
            self._save(object_id, state)

        self._finish_seal(object_id, state)

    def _finish_seal(self, object_id: str, state: dict[str, Any]) -> None:
        staged = state.get("sealing")
        if not staged:
            return

        generation_id = staged["generation_id"]
        ciphertext = self._read_staging_payload(object_id)
        if len(ciphertext) != int(staged.get("ciphertext_size_bytes", len(ciphertext))):
            raise ValueError("Staged ciphertext size does not match metadata")
        nonce = self._unb64(staged["nonce_b64"])
        key = self._unb64(state["key_b64"])
        
        # StorageManager publishes its shard manifest last. If it already exists,
        # this is a recovery after shard publication but before object-state commit.
        try:
            self.storage.get_chunk_metadata(generation_id)
        except FileNotFoundError:
            # No published chunk manifest means any same-ID shard files are
            # leftovers from an interrupted write. They are not committed data.
            for shard_index in range(self.total_shards):
                orphan_path = self.storage._shard_path(generation_id, shard_index)
                if orphan_path.exists():
                    orphan_path.unlink()
            encoded = encode(ciphertext, data_shards=self.data_shards, total_shards=self.total_shards)
            self.storage.store_chunk(generation_id, encoded)
        
        available = self.storage.load_available_shards(generation_id)
        if len(available) < self.data_shards:
            raise RuntimeError("Generation cannot be marked Available: insufficient reconstructible shards")
        metadata = self.storage.get_chunk_metadata(generation_id)
        restored = decode(available, metadata.original_size, self.data_shards, self.total_shards)
        if restored != ciphertext:
            raise RuntimeError("Generation read-back verification failed")
        # Verify AEAD and the staged plaintext before committing the version head.
        plaintext = decrypt(EncryptedPayload(ciphertext=restored, nonce=nonce), key,
                            associated_data=generation_id.encode())
        if hashlib.sha256(plaintext).hexdigest() != staged["plaintext_sha256"]:
            raise RuntimeError("Generation plaintext commitment mismatch")

        generation = {
            "generation_id": generation_id,
            "start_version": staged["start_version"],
            "end_version": staged["end_version"],
            "chunk_root": staged["chunk_root"],
            "plaintext_sha256": staged["plaintext_sha256"],
            "plaintext_size_bytes": staged["plaintext_size_bytes"],
            "nonce_b64": staged["nonce_b64"],
            "entries": staged["entries"],
            "data_shards": self.data_shards,
            "total_shards": self.total_shards,
            "status": "Available",
        }
        # Idempotent commit after a crash/restart.
        if not any(g["generation_id"] == generation_id for g in state["generations"]):
            state["generations"].append(generation)
        entry_by_version = {int(e["version"]): e for e in staged["entries"]}
        for version_entry in state["versions"]:
            version = int(version_entry["version"])
            if version in entry_by_version:
                version_entry["generation_id"] = generation_id
                version_entry["status"] = "Available"
        state["open"] = {"started_at": None, "buffer_size": 0, "entries": []}
        state["sealing"] = None
        self._save(object_id, state)
        # State is committed before deleting recoverable temporary payloads.
        self._cleanup_committed_temporary_files(object_id, state)

    def recover(self, object_id: str) -> dict[str, Any]:
        """Resume a staged seal after restart; otherwise preserve open generation."""
        state = self._load(object_id)
        if state.get("sealing"):
            self._finish_seal(object_id, state)
        state = self._load(object_id)
        self._cleanup_committed_temporary_files(object_id, state)
        return state

    def seal_expired_generations(self, object_id: str, *, now: float | None = None) -> bool:
        """Seal an open generation whose maximum waiting time has elapsed.

        Call from a scheduler/worker if the object must seal even when no more
        Append/GetVersion/ListVersions calls arrive.
        """
        state = self._load(object_id)
        self.recover(object_id)
        state = self._load(object_id)

        opened = state["open"]
        current = time.time() if now is None else float(now)
        if opened["entries"] and current - float(opened["started_at"]) >= self.max_wait_seconds:
            self._seal(object_id, state)
            return True
        return False

    def _read_generation_plaintext(self, state: dict[str, Any], generation: dict[str, Any]) -> bytes:
        generation_id = generation["generation_id"]
        metadata = self.storage.get_chunk_metadata(generation_id)
        available = self.storage.load_available_shards(generation_id)

        if len(available) < self.data_shards:
            raise RuntimeError(f"Generation {generation_id} is not reconstructible")

        ciphertext = decode(available, metadata.original_size, self.data_shards, self.total_shards)
        key = self._unb64(state["key_b64"])
        nonce = self._unb64(generation["nonce_b64"])
        plaintext = decrypt(EncryptedPayload(ciphertext=ciphertext, nonce=nonce), key,
                            associated_data=generation_id.encode())
    
        if hashlib.sha256(plaintext).hexdigest() != generation["plaintext_sha256"]:
            raise ValueError(f"Generation content hash mismatch: {generation_id}")
        if self._chunk_root(plaintext, self.chunk_size) != generation["chunk_root"]:
            raise ValueError(f"Generation chunk root mismatch: {generation_id}")
        return plaintext

    def seal(self, object_id: str) -> bool:
        """Explicitly seal the current non-empty generation (useful for shutdown/tests)."""
        state = self._load(object_id)
        self.recover(object_id)
        state = self._load(object_id)
        if not state["open"]["entries"]:
            return False
        self._seal(object_id, state)
        return True
