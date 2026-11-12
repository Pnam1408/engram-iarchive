"""Encrypted content-addressed chunks and immutable per-version manifests.

The catalog is a logical chunk layer. Generation ciphertext is still encoded and
stored by the existing Reed-Solomon pipeline; this module does not claim to
reduce distributed shard bytes yet.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag

from engram.encryption import EncryptedPayload, decrypt, encrypt
from engram.mmr.hashing import canonical_manifest_bytes
from engram.chunking import Chunker, FixedSizeChunker


class ChunkManifestStore:
    FORMAT = "engram-chunk-manifest-v1"
    LEAF_DOMAIN = b"ENGRAM_CHUNK_MANIFEST_LEAF_V1"
    NODE_DOMAIN = b"ENGRAM_CHUNK_MANIFEST_NODE_V1"

    def __init__(self, root_dir: str | Path, chunker: Chunker) -> None:
        self.root_dir = Path(root_dir)
        self.chunker = chunker
        self.chunk_dir = self.root_dir / "chunks"
        self.manifest_dir = self.root_dir / "manifests"
        self.chunk_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _object_key(object_id: str) -> str:
        return hashlib.sha256(object_id.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _atomic_write(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "wb", dir=path.parent, prefix=f".{path.name}.", delete=False
            ) as stream:
                temp_name = stream.name
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, path)
        finally:
            if temp_name and os.path.exists(temp_name):
                os.unlink(temp_name)

    @classmethod
    def _root(cls, descriptors: list[dict[str, Any]]) -> str:
        leaves = [
            hashlib.sha256(cls.LEAF_DOMAIN + canonical_manifest_bytes(item)).digest()
            for item in descriptors
        ]
        if not leaves:
            leaves = [hashlib.sha256(cls.LEAF_DOMAIN + b"EMPTY").digest()]
        while len(leaves) > 1:
            next_level: list[bytes] = []
            for index in range(0, len(leaves), 2):
                right = leaves[index + 1] if index + 1 < len(leaves) else leaves[index]
                next_level.append(
                    hashlib.sha256(cls.NODE_DOMAIN + leaves[index] + right).digest()
                )
            leaves = next_level
        return leaves[0].hex()

    def _chunk_path(self, object_id: str, digest: str) -> Path:
        return self.chunk_dir / self._object_key(object_id) / f"{digest}.enc"

    def _manifest_path(self, object_id: str, version: int) -> Path:
        return self.manifest_dir / self._object_key(object_id) / f"{version:020d}.json"

    @staticmethod
    def _aad(object_id: str, digest: str) -> bytes:
        return f"ENGRAM_CHUNK_V1:{object_id}:{digest}".encode("utf-8")

    def _put_chunk(
        self, object_id: str, key: bytes, payload: bytes, digest: str
    ) -> None:
        path = self._chunk_path(object_id, digest)
        if path.exists():
            self._read_chunk(object_id, key, digest, len(payload))
            return
        encrypted = encrypt(payload, key, associated_data=self._aad(object_id, digest))
        self._atomic_write(path, encrypted.nonce + encrypted.ciphertext)

    def _read_chunk(
        self, object_id: str, key: bytes, digest: str, expected_size: int
    ) -> bytes:
        raw = self._chunk_path(object_id, digest).read_bytes()
        if len(raw) < 12:
            raise ValueError(f"Truncated encrypted chunk: {digest}")
        payload = decrypt(
            EncryptedPayload(ciphertext=raw[12:], nonce=raw[:12]),
            key,
            associated_data=self._aad(object_id, digest),
        )
        if (
            len(payload) != expected_size
            or hashlib.sha256(payload).hexdigest() != digest
        ):
            raise ValueError(f"Chunk integrity check failed: {digest}")
        return payload

    def put_version(
        self,
        object_id: str,
        version: int,
        data: bytes,
        key: bytes,
        *,
        parent_version: int | None,
    ) -> dict[str, Any]:
        chunks = self.chunker.chunk(data)
        descriptors: list[dict[str, Any]] = []
        for chunk in chunks:
            self._put_chunk(object_id, key, chunk.data, chunk.sha256)
            descriptors.append(
                {
                    "index": chunk.index,
                    "offset": chunk.offset,
                    "size": chunk.size,
                    "sha256": chunk.sha256,
                }
            )
        algorithm = getattr(self.chunker, "algorithm", "fixed-size")
        if algorithm == "fixed-size":
            algorithm = "fixed-size"
            chunker_config = {"chunk_size": int(getattr(self.chunker, "chunk_size"))}
        else:
            config = getattr(self.chunker, "config", None)
            chunker_config = (
                {
                    "min_size": int(config.min_size),
                    "avg_size": int(config.avg_size),
                    "max_size": int(config.max_size),
                }
                if config is not None
                else {}
            )
        manifest: dict[str, Any] = {
            "format": self.FORMAT,
            "object_id": object_id,
            "version": version,
            "parent_version": parent_version,
            "chunker": algorithm,
            "chunker_config": chunker_config,
            "content_size_bytes": len(data),
            "content_sha256": hashlib.sha256(data).hexdigest(),
            "chunk_count": len(descriptors),
            "chunks": descriptors,
        }
        manifest["chunk_manifest_root"] = self._root(descriptors)
        self._atomic_write(
            self._manifest_path(object_id, version),
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        )
        return {
            "chunk_manifest_root": manifest["chunk_manifest_root"],
            "chunk_count": manifest["chunk_count"],
            "content_size_bytes": manifest["content_size_bytes"],
            "content_sha256": manifest["content_sha256"],
            "chunker": manifest["chunker"],
            "chunker_config": manifest["chunker_config"],
        }

    def get_version(self, object_id: str, version: int, key: bytes) -> bytes:
        manifest = self._read_manifest(object_id, version)
        descriptors = manifest["chunks"]
        if manifest["chunk_manifest_root"] != self._root(descriptors):
            raise ValueError(f"Chunk manifest root mismatch for version {version}")
        result = bytearray()
        expected_offset = 0
        for index, descriptor in enumerate(descriptors):
            if descriptor["index"] != index or descriptor["offset"] != expected_offset:
                raise ValueError("Chunk manifest has invalid ordering or offsets")
            chunk = self._read_chunk(
                object_id, key, descriptor["sha256"], int(descriptor["size"])
            )
            result.extend(chunk)
            expected_offset += len(chunk)
        data = bytes(result)
        if len(data) != int(manifest["content_size_bytes"]):
            raise ValueError("Reconstructed version size mismatch")
        if hashlib.sha256(data).hexdigest() != manifest["content_sha256"]:
            raise ValueError("Reconstructed version content hash mismatch")
        return data

    def verify_version(self, object_id: str, version: int, key: bytes) -> bool:
        try:
            manifest = self._read_manifest(object_id, version)
            data = self.get_version(object_id, version, key)
            return (
                manifest["format"] == self.FORMAT
                and int(manifest["version"]) == version
                and int(manifest["chunk_count"]) == len(manifest["chunks"])
                and len(data) == int(manifest["content_size_bytes"])
            )
        except (OSError, ValueError, KeyError, TypeError, IndexError, InvalidTag):
            return False

    def _read_manifest(self, object_id: str, version: int) -> dict[str, Any]:
        manifest = json.loads(
            self._manifest_path(object_id, version).read_text(encoding="utf-8")
        )
        if (
            manifest.get("object_id") != object_id
            or int(manifest.get("version", -1)) != version
        ):
            raise ValueError("Chunk manifest identity mismatch")
        return manifest

    def unique_chunk_bytes(self, object_id: str) -> int:
        directory = self.chunk_dir / self._object_key(object_id)
        return (
            sum(path.stat().st_size for path in directory.glob("*.enc"))
            if directory.exists()
            else 0
        )

    def unique_chunk_count(self, object_id: str) -> int:
        directory = self.chunk_dir / self._object_key(object_id)
        return len(list(directory.glob("*.enc"))) if directory.exists() else 0

    def logical_chunk_bytes(self, object_id: str, version: int) -> int:
        manifest = self._read_manifest(object_id, version)
        return sum(int(item["size"]) for item in manifest["chunks"])
