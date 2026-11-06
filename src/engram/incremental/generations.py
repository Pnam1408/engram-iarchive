#engram/incremental/generations.py
"""Generation batching, sealing, publication, and crash recovery."""
from __future__ import annotations

import hashlib
import time

from engram.encryption import EncryptedPayload, decrypt, encrypt
from engram.erasure_coding import EncodedShards, decode, encode
from engram.storage import ChunkCommitment
from engram.mmr import MMRAccumulator
from engram.mmr.hashing import canonical_manifest_bytes

from .contracts import BufferAccess, GenerationAccess, StateAccess
from .types import GenerationRecord, ObjectState, StagedGeneration, VersionEntry
from .manifest import build_version_manifest

class GenerationMixin(StateAccess, BufferAccess, GenerationAccess):
    def _seal(self, object_id: str, state: ObjectState) -> None:
        """Prepare a generation, then run the remaining seal stages.

        The individual stage methods are intentionally separate so benchmark
        code can time them independently without putting timers in this class.
        """
        if state.get("sealing") is None:
            opened = state["open"]
            if not opened["entries"]:
                return
            plaintext = self._read_buffer(object_id, state)
            entries = [entry.copy() for entry in opened["entries"]]
            start_version = int(entries[0]["version"])
            end_version = int(entries[-1]["version"])
            chunk_root = self._chunk_root(plaintext, self.chunk_size)
            object_hash = self._object_key(object_id)[:10]
            generation_id = (
                f"g_{object_hash}_{start_version}_{end_version}_{chunk_root[:16]}"
            )
            self._encrypt_generation(
                object_id, state, plaintext, entries,
                generation_id, start_version, end_version, chunk_root,
            )
        self._finish_seal(object_id, state)

    def _encrypt_generation(
        self,
        object_id: str,
        state: ObjectState,
        plaintext: bytes,
        entries: list[VersionEntry],
        generation_id: str,
        start_version: int,
        end_version: int,
        chunk_root: str,
    ) -> None:
        """Encrypt plaintext and durably stage ciphertext + its metadata."""
        key = self._unb64(state["key_b64"])
        encrypted = encrypt(
            plaintext, key, associated_data=generation_id.encode()
        )
        staged: StagedGeneration = {
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
        # Payload first, metadata second: recovery only trusts a staged record
        # after the corresponding ciphertext has been durably written.
        self._write_staging_payload(object_id, encrypted.ciphertext)
        state["sealing"] = staged
        self._save(object_id, state)

    def _encode_generation(self, ciphertext: bytes) -> EncodedShards:
        """Apply Reed-Solomon encoding; performs no filesystem writes."""
        return encode(
            ciphertext,
            data_shards=self.data_shards,
            total_shards=self.total_shards,
        )

    def _commit_generation(self, encoded: EncodedShards) -> ChunkCommitment:
        """Build Merkle commitments for encoded shards (CPU-only stage)."""
        return self.storage.build_commitment(list(encoded.shards))

    def _write_generation(
            self, 
            generation_id: str, 
            encoded: EncodedShards, 
            commitment: ChunkCommitment
        ) -> None:
        """Write shard files and publish the manifest last."""
        shards = list(encoded.shards)
        shard_records = self.storage.write_shards(generation_id, shards)
        manifest = self.storage.build_manifest(
            chunk_id=generation_id,
            encoded=encoded,
            shards=shards,
            shard_records=shard_records,
            commitment=commitment,
        )
        self.storage.persist_manifest(generation_id, manifest)

    def _verify_generation(
        self,
        object_id: str,
        state: ObjectState,
        staged: StagedGeneration,
        ciphertext: bytes,
    ) -> bytes:
        """Read shards back, reconstruct ciphertext, and verify AEAD/content."""
        generation_id = staged["generation_id"]
        available = self.storage.load_available_shards(generation_id)
        if len(available) < self.data_shards:
            raise RuntimeError(
                "Generation cannot be marked Available: "
                "insufficient reconstructible shards"
            )
        metadata = self.storage.get_chunk_metadata(generation_id)
        restored = decode(
            available, metadata.original_size,
            self.data_shards, self.total_shards,
        )
        if restored != ciphertext:
            raise RuntimeError("Generation read-back verification failed")

        nonce = self._unb64(staged["nonce_b64"])
        key = self._unb64(state["key_b64"])
        plaintext = decrypt(
            EncryptedPayload(ciphertext=restored, nonce=nonce),
            key,
            associated_data=generation_id.encode(),
        )
        if hashlib.sha256(plaintext).hexdigest() != staged["plaintext_sha256"]:
            raise RuntimeError("Generation plaintext commitment mismatch")
        self._validate_staged_generation(state, staged, plaintext)
        return plaintext

    def _publish_generation_state(
        self,
        object_id: str,
        state: ObjectState,
        staged: StagedGeneration,
    ) -> None:
        """Commit generation/version metadata after successful verification."""
        generation_id = staged["generation_id"]
        generation: GenerationRecord = {
            "generation_id": generation_id,
            "start_version": staged["start_version"],
            "end_version": staged["end_version"],
            "chunk_root": staged["chunk_root"],
            "chunk_size": self.chunk_size,
            "plaintext_sha256": staged["plaintext_sha256"],
            "plaintext_size_bytes": staged["plaintext_size_bytes"],
            "nonce_b64": staged["nonce_b64"],
            "entries": staged["entries"],
            "data_shards": self.data_shards,
            "total_shards": self.total_shards,
            "status": "Available",
        }
        existing_generation = next(
            (g for g in state["generations"] if g["generation_id"] == generation_id),
            None,
        )
        if existing_generation is not None and existing_generation != generation:
            raise ValueError(f"Conflicting committed generation metadata: {generation_id}")
        if existing_generation is None:
            state["generations"].append(generation)

        # Work on a reconstructed accumulator so any validation failure leaves
        # the persisted MMR untouched. Manifest append and Available publication
        # are committed together by the single atomic object-state write below.
        accumulator = MMRAccumulator.from_dict(state["mmr"])
        version_records = {
            int(item["version"]): item for item in state["versions"]
        }
        if len(version_records) != len(state["versions"]):
            raise ValueError("Duplicate version records during generation publication")

        for staged_entry in sorted(staged["entries"], key=lambda item: int(item["version"])):
            version = int(staged_entry["version"])
            record = version_records.get(version)
            if record is None:
                raise ValueError(f"Version {version} is missing during publication")
            if (
                int(record["delta_size_bytes"]) != int(staged_entry["delta_size_bytes"])
                or record["delta_sha256"] != staged_entry["delta_sha256"]
            ):
                raise ValueError(f"Version {version} metadata differs from staged entry")
            manifest = build_version_manifest(object_id, staged_entry, generation)

            if version == accumulator.last_version + 1:
                accumulator.append(manifest, version=version)
            elif version <= accumulator.last_version:
                # Recovery may retry a publication already reflected in MMR.
                committed_manifest = accumulator.to_dict()["manifests"][version - 1]
                if canonical_manifest_bytes(committed_manifest) != canonical_manifest_bytes(manifest):
                    raise ValueError(f"Conflicting MMR manifest for already committed version {version}")
            else:
                raise ValueError(
                    f"MMR version gap: expected {accumulator.last_version + 1}, got {version}"
                )

            if record.get("generation_id") not in (None, generation_id):
                raise ValueError(f"Version {version} already belongs to another generation")
            record["generation_id"] = generation_id
            record["status"] = "Available"

        state["mmr"] = accumulator.to_dict()
        state["open"] = {"started_at": None, "buffer_size": 0, "entries": []}
        state["sealing"] = None
        self._save(object_id, state)
        # State commit precedes cleanup so recovery can safely retry cleanup.
        self._cleanup_committed_temporary_files(object_id, state)

    def _finish_seal(self, object_id: str, state: ObjectState) -> None:
        """Resume the encode -> write -> verify -> commit stages idempotently."""
        staged = state.get("sealing")
        if not staged:
            return

        generation_id = staged["generation_id"]
        ciphertext = self._read_staging_payload(object_id)
        expected_size = int(staged["ciphertext_size_bytes"])
        if len(ciphertext) != expected_size:
            raise ValueError("Staged ciphertext size does not match metadata")

        # A manifest means the shard write stage completed before a crash.
        try:
            self.storage.get_chunk_metadata(generation_id)
        except FileNotFoundError:
            # Remove uncommitted shard leftovers from an interrupted write.
            for shard_index in range(self.total_shards):
                orphan_path = self.storage._shard_path(generation_id, shard_index)
                if orphan_path.exists():
                    orphan_path.unlink()
            encoded = self._encode_generation(ciphertext)
            commitment = self._commit_generation(encoded)
            self._write_generation(generation_id, encoded, commitment)

        self._verify_generation(object_id, state, staged, ciphertext)
        self._publish_generation_state(object_id, state, staged)

    def recover(self, object_id: str) -> ObjectState:
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
        if (
            opened["entries"]
            and opened["started_at"] is not None
            and current - opened["started_at"] >= self.max_wait_seconds
        ):
            self._seal(object_id, state)
            return True
        return False

    def _read_generation_plaintext(
        self,
        state: ObjectState,
        generation: GenerationRecord,
    ) -> bytes:
        generation_id = generation["generation_id"]

        metadata = self.storage.get_chunk_metadata(generation_id)
        available = self.storage.load_available_shards(generation_id)

        data_shards = int(generation.get("data_shards", self.data_shards))
        total_shards = int(generation.get("total_shards", self.total_shards))
        chunk_size = int(generation.get("chunk_size", self.chunk_size))

        if not (1 <= data_shards < total_shards <= 255):
            raise ValueError(f"Invalid Reed-Solomon configuration: {generation_id}")

        if len(available) < data_shards:
            raise RuntimeError(
                f"Generation {generation_id} is not reconstructible"
            )

        ciphertext = decode(
            available,
            metadata.original_size,
            data_shards,
            total_shards,
        )

        key = self._unb64(state["key_b64"])
        nonce = self._unb64(generation["nonce_b64"])

        plaintext = decrypt(
            EncryptedPayload(ciphertext=ciphertext, nonce=nonce),
            key,
            associated_data=generation_id.encode(),
        )

        if len(plaintext) != int(generation["plaintext_size_bytes"]):
            raise ValueError(
                f"Generation plaintext size mismatch: {generation_id}"
            )

        if hashlib.sha256(plaintext).hexdigest() != generation["plaintext_sha256"]:
            raise ValueError(
                f"Generation content hash mismatch: {generation_id}"
            )

        if self._chunk_root(plaintext, chunk_size) != generation["chunk_root"]:
            raise ValueError(
                f"Generation chunk root mismatch: {generation_id}"
            )

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

    def _validate_staged_generation(
        self,
        state: ObjectState,
        staged: StagedGeneration,
        plaintext: bytes,
    ) -> None:
        """Validate staged generation metadata before publishing/committing."""
        entries = staged.get("entries")

        if not isinstance(entries, list) or not entries:
            raise ValueError("Staged generation must contain entries")

        versions = [int(entry["version"]) for entry in entries]

        if versions != list(range(versions[0], versions[-1] + 1)):
            raise ValueError("Generation versions must be unique and contiguous")

        if versions[0] != int(staged["start_version"]):
            raise ValueError("Generation start_version mismatch")

        if versions[-1] != int(staged["end_version"]):
            raise ValueError("Generation end_version mismatch")

        if sum(int(entry["delta_size_bytes"]) for entry in entries) != len(plaintext):
            raise ValueError("Generation entries size does not match plaintext size")

        # Every staged entry must match an existing version record.
        records: dict[int, VersionEntry] = {}

        for record in state["versions"]:
            version = int(record["version"])

            if version in records:
                raise ValueError(f"Duplicate version record: {version}")

            records[version] = record

        generation_id = staged["generation_id"]

        for entry in entries:
            version = int(entry["version"])
            record = records.get(version)

            if record is None:
                raise ValueError(f"Version {version} is missing from object state")

            if int(record["delta_size_bytes"]) != int(entry["delta_size_bytes"]):
                raise ValueError(f"Version {version} delta size mismatch")

            if record["delta_sha256"] != entry["delta_sha256"]:
                raise ValueError(f"Version {version} delta hash mismatch")

            existing_generation_id = record.get("generation_id")

            if existing_generation_id not in (None, generation_id):
                raise ValueError(
                    f"Version {version} already belongs to another generation"
                )

        # A version must not already belong to a different committed generation.
        staged_version_set = set(versions)

        for generation in state["generations"]:
            if generation["generation_id"] == generation_id:
                continue

            for entry in generation["entries"]:
                if int(entry["version"]) in staged_version_set:
                    raise ValueError(
                        f"Version {entry['version']} already exists in another generation"
                    )