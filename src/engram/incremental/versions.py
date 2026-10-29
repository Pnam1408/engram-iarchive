"""Version listing, retrieval, and history verification APIs."""
from __future__ import annotations

import hashlib
from typing import Any

class VersionMixin:
    def list_versions(self, object_id: str) -> list[dict[str, Any]]:
        self.recover(object_id)
        state = self._load(object_id)
        return [dict(v) for v in sorted(state["versions"], key=lambda v: int(v["version"]))]

    def ListVersions(self, object_id: str) -> list[dict[str, Any]]:
        return self.list_versions(object_id)

    def get_version(self, object_id: str, version: int) -> bytes:
        """Retrieve a committed object version by replaying ordered generations."""
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise ValueError("version must be a positive integer")

        self.recover(object_id)
        state = self._load(object_id)

        version_record = next((v for v in state["versions"] if int(v["version"]) == version), None)
        if version_record is None:
            raise KeyError(f"Version {version} does not exist for object {object_id!r}")
        if version_record["status"] != "Available":
            raise RuntimeError(f"Version {version} is {version_record['status']}, not Available")
        
        object_bytes = bytearray()
        target_reached = False
        for generation in sorted(state["generations"], key=lambda g: int(g["start_version"])):
            if int(generation["start_version"]) > version:
                break
            plain = self._read_generation_plaintext(state, generation)
            offset = 0
            for entry in generation["entries"]:
                size = int(entry["delta_size_bytes"])
                delta = plain[offset:offset + size]
                offset += size
                if len(delta) != size or hashlib.sha256(delta).hexdigest() != entry["delta_sha256"]:
                    raise ValueError(f"Delta integrity check failed at version {entry['version']}")
                object_bytes.extend(delta)
                if int(entry["version"]) == version:
                    target_reached = True
                    return bytes(object_bytes)
        if not target_reached:
            raise RuntimeError(f"Version {version} has no committed generation")
        return bytes(object_bytes)

    def GetVersion(self, object_id: str, version: int) -> bytes:
        return self.get_version(object_id, version)

    def verify_history(self, object_id: str) -> bool:
        """Verify ordered versions, generation commitments, and all delta hashes."""
        self.recover(object_id)
        state = self._load(object_id)

        expected_versions = list(range(1, int(state["next_version"])))
        actual_versions = [int(v["version"]) for v in sorted(state["versions"], key=lambda v: int(v["version"]))]
        if actual_versions != expected_versions:
            return False

        available_generation_versions: list[int] = []
        try:
            for generation in sorted(state["generations"], key=lambda g: int(g["start_version"])):
                plain = self._read_generation_plaintext(state, generation)
                if hashlib.sha256(plain).hexdigest() != generation["plaintext_sha256"]:
                    return False
                if self._chunk_root(plain, self.chunk_size) != generation["chunk_root"]:
                    return False
                if sum(int(e["delta_size_bytes"]) for e in generation["entries"]) != len(plain):
                    return False
                offset = 0
                for entry in generation["entries"]:
                    size = int(entry["delta_size_bytes"])
                    delta = plain[offset:offset + size]
                    offset += size
                    if hashlib.sha256(delta).hexdigest() != entry["delta_sha256"]:
                        return False
                    available_generation_versions.append(int(entry["version"]))
        except (OSError, ValueError, RuntimeError, KeyError):
            # Missing/corrupt generation data is a failed history verification.
            return False
        # Pending versions are valid history records, but not yet retrievable.
        pending_versions = [int(v["version"]) for v in state["versions"] if v["status"] != "Available"]
        if available_generation_versions != sorted(available_generation_versions):
            return False
        if set(available_generation_versions) & set(pending_versions):
            return False
        return sorted(available_generation_versions + pending_versions) == expected_versions

    def VerifyHistory(self, object_id: str) -> bool:
        return self.verify_history(object_id)
