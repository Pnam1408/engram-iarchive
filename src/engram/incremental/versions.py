# engram/incremental/versions.py
"""Version listing, retrieval, and history verification APIs."""

from __future__ import annotations

import copy
import hashlib

from cryptography.exceptions import InvalidTag

from engram.mmr import (
    MMRAccumulator,
    generate_consistency_proof,
    generate_inclusion_proof,
)
from engram.mmr.hashing import canonical_manifest_bytes

from .contracts import (
    BufferAccess,
    GenerationAccess,
    StateAccess,
    VersionAccess,
)
from .types import GenerationRecord, ObjectState, VersionEntry
from .manifest import build_version_manifest


class VersionMixin(StateAccess, BufferAccess, GenerationAccess, VersionAccess):
    @staticmethod
    def _generation_entries(generation: GenerationRecord) -> list[VersionEntry]:
        """Validate and return entries belonging to one generation."""
        entries = generation.get("entries")

        if not isinstance(entries, list) or not entries:
            raise ValueError("Generation must contain at least one entry")

        versions = [int(entry["version"]) for entry in entries]

        if versions != list(range(versions[0], versions[-1] + 1)):
            raise ValueError("Generation entries must be contiguous and ordered")

        if versions[0] != int(generation["start_version"]):
            raise ValueError("Generation start_version mismatch")

        if versions[-1] != int(generation["end_version"]):
            raise ValueError("Generation end_version mismatch")

        for entry in entries:
            operation = entry.get("operation", "append")
            payload_size = int(entry["delta_size_bytes"])
            storage_size = int(entry.get("storage_size_bytes", payload_size))
            if operation not in {"append", "insert", "delete"}:
                raise ValueError(f"Unsupported operation: {operation!r}")
            if payload_size < 0 or storage_size <= 0:
                raise ValueError("Invalid operation payload/storage size")
            if operation in {"append", "insert"} and payload_size <= 0:
                raise ValueError("Append/insert payload must not be empty")
            if operation == "delete" and (
                payload_size != 0 or int(entry.get("delete_length", 0)) <= 0
            ):
                raise ValueError(
                    "Delete operation must have an empty payload and positive length"
                )
            if operation == "append" and entry.get("offset") is not None:
                raise ValueError("Append operation must not specify an offset")
            if operation in {"insert", "delete"}:
                offset = entry.get("offset")
                if (
                    not isinstance(offset, int)
                    or isinstance(offset, bool)
                    or offset < 0
                ):
                    raise ValueError(f"Invalid {operation} offset")

        return entries

    def _ordered_generations(
        self,
        state: ObjectState,
    ) -> list[GenerationRecord]:
        """Validate generation ranges and return them in version order."""
        generations = sorted(
            state["generations"],
            key=lambda generation: int(generation["start_version"]),
        )

        expected_version = 1

        for generation in generations:
            entries = self._generation_entries(generation)

            start = int(generation["start_version"])
            end = int(generation["end_version"])

            if start != expected_version:
                raise ValueError(
                    f"Generation version gap or overlap: "
                    f"expected {expected_version}, found {start}"
                )

            if end < start:
                raise ValueError("Invalid generation version range")

            if generation.get("status") != "Available":
                raise ValueError(
                    f"Generation {generation['generation_id']} is not Available"
                )

            expected_version = end + 1

        return generations

    def _validate_version_generation_mapping(
        self,
        state: ObjectState,
        generations: list[GenerationRecord],
    ) -> tuple[dict[int, str], list[int]]:
        """Check consistency between version records and generation entries."""
        version_records: dict[int, VersionEntry] = {}

        for record in state["versions"]:
            version = int(record["version"])

            if version in version_records:
                raise ValueError(f"Duplicate version record: {version}")

            version_records[version] = record

        version_to_generation: dict[int, str] = {}

        for generation in generations:
            generation_id = generation["generation_id"]

            for entry in self._generation_entries(generation):
                version = int(entry["version"])

                if version in version_to_generation:
                    raise ValueError(
                        f"Version {version} appears in multiple generations"
                    )

                record = version_records.get(version)

                if record is None:
                    raise ValueError(
                        f"Version {version} is missing from version records"
                    )

                if record.get("status") != "Available":
                    raise ValueError(
                        f"Version {version} is in a generation but not Available"
                    )

                if record.get("generation_id") != generation_id:
                    raise ValueError(f"Version {version} generation_id mismatch")

                if int(record["delta_size_bytes"]) != int(entry["delta_size_bytes"]):
                    raise ValueError(f"Version {version} delta size mismatch")

                if record["delta_sha256"] != entry["delta_sha256"]:
                    raise ValueError(f"Version {version} delta hash mismatch")
                for field in (
                    "operation",
                    "offset",
                    "delete_length",
                    "storage_size_bytes",
                ):
                    if record.get(
                        field,
                        {
                            "operation": "append",
                            "offset": None,
                            "delete_length": 0,
                            "storage_size_bytes": record["delta_size_bytes"],
                        }[field],
                    ) != entry.get(
                        field,
                        {
                            "operation": "append",
                            "offset": None,
                            "delete_length": 0,
                            "storage_size_bytes": entry["delta_size_bytes"],
                        }[field],
                    ):
                        raise ValueError(f"Version {version} {field} mismatch")

                version_to_generation[version] = generation_id

        pending_versions: list[int] = []

        for version, record in version_records.items():
            status = record.get("status")

            if status == "PENDING":
                if record.get("generation_id") is not None:
                    raise ValueError(f"Pending version {version} has a generation_id")
                pending_versions.append(version)

            elif status == "Available":
                if version not in version_to_generation:
                    raise ValueError(f"Available version {version} has no generation")

            else:
                raise ValueError(
                    f"Unsupported status for version {version}: {status!r}"
                )

        pending_versions.sort()

        return version_to_generation, pending_versions

    def list_versions(self, object_id: str) -> list[VersionEntry]:
        self.recover(object_id)
        state = self._load(object_id)

        return [
            version.copy()
            for version in sorted(
                state["versions"],
                key=lambda item: int(item["version"]),
            )
        ]

    def list_generations(self, object_id: str) -> list[GenerationRecord]:
        """List committed generations in version order.

        Recovery is performed before reading metadata. Only published,
        reconstructible generations are returned; data still in the open
        buffer or an unfinished sealing stage is not exposed as a generation.
        Returned records are deep copies so callers cannot mutate store state.
        """
        self.recover(object_id)
        state = self._load(object_id)
        generations = self._ordered_generations(state)
        return copy.deepcopy(generations)

    @staticmethod
    def _apply_operation(
        current: bytearray, entry: VersionEntry, payload: bytes
    ) -> None:
        operation = entry.get("operation", "append")
        if operation == "append":
            current.extend(payload)
            return
        offset = entry.get("offset")
        if (
            not isinstance(offset, int)
            or isinstance(offset, bool)
            or not 0 <= offset <= len(current)
        ):
            raise ValueError(
                f"Invalid {operation} offset at version {entry['version']}"
            )
        if operation == "insert":
            current[offset:offset] = payload
            return
        if operation == "delete":
            raw_length = entry.get("delete_length", 0)
            if not isinstance(raw_length, int) or isinstance(raw_length, bool):
                raise ValueError(f"Invalid delete length at version {entry['version']}")
            length = raw_length
            if length <= 0 or offset + length > len(current):
                raise ValueError(f"Invalid delete range at version {entry['version']}")
            del current[offset : offset + length]
            return
        raise ValueError(f"Unsupported operation: {operation!r}")

    def get_version(self, object_id: str, version: int) -> bytes:
        """Reconstruct a committed version by replaying operations from genesis."""
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise ValueError("version must be a positive integer")

        self.recover(object_id)
        state = self._load(object_id)
        version_record = next(
            (
                record
                for record in state["versions"]
                if int(record["version"]) == version
            ),
            None,
        )
        if version_record is None:
            raise KeyError(f"Version {version} does not exist for object {object_id!r}")
        if version_record["status"] != "Available":
            raise RuntimeError(
                f"Version {version} is {version_record['status']}, not Available"
            )

        generations = self._ordered_generations(state)
        version_to_generation, _ = self._validate_version_generation_mapping(
            state, generations
        )
        if version not in version_to_generation:
            raise RuntimeError(f"Version {version} has no committed generation")

        current = bytearray()
        expected_version = 1
        for generation in generations:
            if int(generation["start_version"]) > version:
                break
            entries = self._generation_entries(generation)
            plain = self._read_generation_plaintext(state, generation)
            expected_size = sum(
                int(entry.get("storage_size_bytes", entry["delta_size_bytes"]))
                for entry in entries
            )
            if len(plain) != expected_size:
                raise ValueError(
                    f"Generation size does not match entries: {generation['generation_id']}"
                )
            cursor = 0
            for entry in entries:
                entry_version = int(entry["version"])
                if entry_version != expected_version:
                    raise ValueError(
                        f"Unexpected version {entry_version}; expected {expected_version}"
                    )
                stored_size = int(
                    entry.get("storage_size_bytes", entry["delta_size_bytes"])
                )
                stored = plain[cursor : cursor + stored_size]
                cursor += stored_size
                if len(stored) != stored_size:
                    raise ValueError(
                        f"Operation record size mismatch at version {entry_version}"
                    )
                operation = entry.get("operation", "append")
                payload_size = int(entry["delta_size_bytes"])
                if operation == "delete":
                    if stored != b"\x00":
                        raise ValueError(
                            f"Delete marker mismatch at version {entry_version}"
                        )
                    payload = b""
                else:
                    payload = stored
                    if len(payload) != payload_size:
                        raise ValueError(
                            f"Operation payload size mismatch at version {entry_version}"
                        )
                if hashlib.sha256(payload).hexdigest() != entry["delta_sha256"]:
                    raise ValueError(
                        f"Operation payload integrity check failed at version {entry_version}"
                    )
                self._apply_operation(current, entry, payload)
                expected_version += 1
                if entry_version == version:
                    return bytes(current)
        raise RuntimeError(f"Version {version} could not be reconstructed")

    def get_version_from_chunks(self, object_id: str, version: int) -> bytes:
        """Reconstruct a version from its encrypted content-addressed chunks."""
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise ValueError("version must be a positive integer")
        self.recover(object_id)
        state = self._load(object_id)
        record = next(
            (item for item in state["versions"] if int(item["version"]) == version),
            None,
        )
        if record is None:
            raise KeyError(f"Version {version} does not exist for object {object_id!r}")
        if record.get("status") != "Available":
            raise RuntimeError(f"Version {version} is not Available")
        if not record.get("chunk_manifest_root"):
            raise RuntimeError(f"Version {version} predates chunk-manifest indexing")
        manifest = self.chunk_catalog._read_manifest(object_id, version)
        for field in (
            "chunk_manifest_root",
            "chunk_count",
            "content_size_bytes",
            "content_sha256",
            "chunker",
            "chunker_config",
        ):
            if record.get(field) != manifest.get(field):
                raise ValueError(f"Version {version} chunk manifest {field} mismatch")
        return self.chunk_catalog.get_version(
            object_id, version, self._unb64(state["key_b64"])
        )

    def get_chunk_manifest(self, object_id: str, version: int) -> dict[str, object]:
        """Return a defensive copy of the persisted logical chunk manifest."""
        import copy

        self.recover(object_id)
        state = self._load(object_id)
        record = next(
            (item for item in state["versions"] if int(item["version"]) == version),
            None,
        )
        if record is None or record.get("status") != "Available":
            raise KeyError(f"Available version {version} does not exist")
        path = self.chunk_catalog._manifest_path(object_id, version)
        return copy.deepcopy(self.chunk_catalog._read_manifest(object_id, version))

    def get_chunk_storage_stats(self, object_id: str, version: int) -> dict[str, int]:
        """Report logical bytes and unique encrypted chunk bytes for this object."""
        manifest = self.get_chunk_manifest(object_id, version)
        return {
            "content_size_bytes": int(manifest["content_size_bytes"]),
            "logical_chunk_bytes": self.chunk_catalog.logical_chunk_bytes(
                object_id, version
            ),
            "unique_encrypted_chunk_bytes": self.chunk_catalog.unique_chunk_bytes(
                object_id
            ),
            "chunk_count": int(manifest["chunk_count"]),
            "unique_chunk_count": self.chunk_catalog.unique_chunk_count(object_id),
        }

    def get_mmr_root(self, object_id: str) -> str:
        """Return the current per-object MMR root."""
        if not self._state_path(object_id).exists():
            return MMRAccumulator().root.hex()

        self.recover(object_id)
        state = self._load(object_id)
        return MMRAccumulator.from_dict(state["mmr"]).root.hex()

    def get_inclusion_proof(self, object_id: str, version: int) -> dict[str, object]:
        """Return an inclusion proof for one Available version (leaf index = version - 1)."""
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise ValueError("version must be a positive integer")
        self.recover(object_id)
        state = self._load(object_id)
        record = next(
            (item for item in state["versions"] if int(item["version"]) == version),
            None,
        )
        if record is None:
            raise KeyError(f"Version {version} does not exist for object {object_id!r}")
        if record.get("status") != "Available":
            raise RuntimeError(f"Version {version} is not Available")
        accumulator = MMRAccumulator.from_dict(state["mmr"])
        return generate_inclusion_proof(accumulator, version - 1)

    def get_consistency_proof(self, object_id: str, old_size: int) -> dict[str, object]:
        """Prove that the current MMR extends a previously observed prefix size."""
        if not isinstance(old_size, int) or isinstance(old_size, bool) or old_size < 0:
            raise ValueError("old_size must be a non-negative integer")
        self.recover(object_id)
        state = self._load(object_id)
        accumulator = MMRAccumulator.from_dict(state["mmr"])
        return generate_consistency_proof(accumulator, old_size)

    def verify_history(self, object_id: str) -> bool:
        """Verify version continuity, generation metadata, and all delta hashes."""
        try:
            self.recover(object_id)
            state = self._load(object_id)

            next_version = int(state["next_version"])

            if next_version < 1:
                return False

            expected_versions = list(range(1, next_version))

            version_numbers = [int(record["version"]) for record in state["versions"]]

            if sorted(version_numbers) != expected_versions:
                return False

            if len(set(version_numbers)) != len(version_numbers):
                return False

            generations = self._ordered_generations(state)

            version_to_generation, pending_versions = (
                self._validate_version_generation_mapping(
                    state,
                    generations,
                )
            )

            committed_versions = sorted(version_to_generation)

            if committed_versions != list(range(1, len(committed_versions) + 1)):
                return False

            # Pending versions must form a contiguous tail after committed data.
            expected_pending = list(
                range(
                    len(committed_versions) + 1,
                    next_version,
                )
            )

            if pending_versions != expected_pending:
                return False

            # Every pending version must belong to the open generation.
            open_entries = state["open"]["entries"]
            open_versions = [int(entry["version"]) for entry in open_entries]

            if open_versions != pending_versions:
                return False

            reconstructed = bytearray()
            for generation in generations:
                entries = self._generation_entries(generation)
                plain = self._read_generation_plaintext(state, generation)
                expected_size = sum(
                    int(entry.get("storage_size_bytes", entry["delta_size_bytes"]))
                    for entry in entries
                )
                if len(plain) != expected_size:
                    return False
                offset = 0
                for entry in entries:
                    storage_size = int(
                        entry.get("storage_size_bytes", entry["delta_size_bytes"])
                    )
                    stored = plain[offset : offset + storage_size]
                    offset += storage_size
                    if len(stored) != storage_size:
                        return False
                    operation = entry.get("operation", "append")
                    payload = b"" if operation == "delete" else stored
                    if operation == "delete" and stored != b"\x00":
                        return False
                    if len(payload) != int(entry["delta_size_bytes"]):
                        return False
                    if hashlib.sha256(payload).hexdigest() != entry["delta_sha256"]:
                        return False
                    self._apply_operation(reconstructed, entry, payload)

            # Pending operation metadata must agree between the version index
            # and the open-generation entry list.
            records_by_version = {
                int(record["version"]): record for record in state["versions"]
            }
            for entry in open_entries:
                version = int(entry["version"])
                record = records_by_version.get(version)
                if record is None:
                    return False
                for field in (
                    "operation",
                    "offset",
                    "delete_length",
                    "delta_size_bytes",
                    "delta_sha256",
                    "storage_size_bytes",
                ):
                    default = {
                        "operation": "append",
                        "offset": None,
                        "delete_length": 0,
                    }.get(field)
                    if field == "storage_size_bytes":
                        record_value = record.get(field, record["delta_size_bytes"])
                        entry_value = entry.get(field, entry["delta_size_bytes"])
                    elif field in {"operation", "offset", "delete_length"}:
                        record_value = record.get(field, default)
                        entry_value = entry.get(field, default)
                    else:
                        record_value = record[field]
                        entry_value = entry[field]
                    if record_value != entry_value:
                        return False

            # Check and replay the open buffer using the same operation semantics.
            if open_entries:
                open_plaintext = self._read_buffer(object_id, state)
                expected_open_size = sum(
                    int(entry.get("storage_size_bytes", entry["delta_size_bytes"]))
                    for entry in open_entries
                )
                if len(open_plaintext) != expected_open_size:
                    return False
                offset = 0
                for entry in open_entries:
                    storage_size = int(
                        entry.get("storage_size_bytes", entry["delta_size_bytes"])
                    )
                    stored = open_plaintext[offset : offset + storage_size]
                    offset += storage_size
                    if len(stored) != storage_size:
                        return False
                    operation = entry.get("operation", "append")
                    payload = b"" if operation == "delete" else stored
                    if operation == "delete" and stored != b"\x00":
                        return False
                    if len(payload) != int(entry["delta_size_bytes"]):
                        return False
                    if hashlib.sha256(payload).hexdigest() != entry["delta_sha256"]:
                        return False
                    self._apply_operation(reconstructed, entry, payload)
            elif int(state["open"].get("buffer_size", 0)) != 0:
                return False

            # Independently rebuild the expected version manifests from the
            # committed generation/version metadata and compare them byte-for-byte.
            accumulator = MMRAccumulator.from_dict(state["mmr"])
            if accumulator.leaf_count != len(committed_versions):
                return False
            if accumulator.last_version != (
                committed_versions[-1] if committed_versions else 0
            ):
                return False

            generation_by_id = {
                generation["generation_id"]: generation for generation in generations
            }
            records_by_version = {
                int(record["version"]): record for record in state["versions"]
            }
            expected_accumulator = MMRAccumulator()
            for version in committed_versions:
                record = records_by_version[version]
                generation_id = version_to_generation[version]
                generation = generation_by_id[generation_id]
                expected_manifest = build_version_manifest(
                    object_id, record, generation
                )
                if record.get("chunk_manifest_root") is not None:
                    key = self._unb64(state["key_b64"])
                    if not self.chunk_catalog.verify_version(object_id, version, key):
                        return False
                    chunk_manifest = self.chunk_catalog._read_manifest(
                        object_id, version
                    )
                    for field in (
                        "chunk_manifest_root",
                        "chunk_count",
                        "content_size_bytes",
                        "content_sha256",
                        "chunker",
                        "chunker_config",
                    ):
                        if record.get(field) != chunk_manifest.get(field):
                            return False
                expected_accumulator.append(expected_manifest, version=version)

            stored_manifests = accumulator.to_dict()["manifests"]
            expected_manifests = expected_accumulator.to_dict()["manifests"]
            if len(stored_manifests) != len(expected_manifests):
                return False
            if any(
                canonical_manifest_bytes(actual) != canonical_manifest_bytes(expected)
                for actual, expected in zip(
                    stored_manifests, expected_manifests, strict=True
                )
            ):
                return False
            if accumulator.root != expected_accumulator.root:
                return False

            return True

        except (
            OSError,
            ValueError,
            RuntimeError,
            KeyError,
            TypeError,
            IndexError,
            InvalidTag,
        ):
            return False
