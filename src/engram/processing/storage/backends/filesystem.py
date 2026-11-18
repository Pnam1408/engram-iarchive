from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from pathlib import Path

from engram.processing.exceptions import (
    InvalidStorageKeyError,
    StorageConflictError,
    StorageIOError,
    StorageNotFoundError,
)
from engram.processing.storage.base import StorageBackend
from engram.processing.storage.config import FilesystemStorageConfig
from engram.processing.storage.models import StorageObject


class FilesystemStorageBackend(StorageBackend):
    """Store immutable objects on the local filesystem."""

    def __init__(self, config: FilesystemStorageConfig) -> None:
        self._config = config
        self._root = config.root_dir

        try:
            self._root.mkdir(parents=True, exist_ok=True)

            if not self._root.is_dir():
                raise StorageIOError(f"Storage root is not a directory: {self._root}")
        except StorageIOError:
            raise
        except OSError as exc:
            raise StorageIOError(
                f"Cannot initialize storage root: {self._root}"
            ) from exc

    @staticmethod
    def _validate_key(key: str) -> tuple[str, ...]:
        if not isinstance(key, str) or not key:
            raise InvalidStorageKeyError("Storage key must be a non-empty string")

        if "\\" in key or "\x00" in key:
            raise InvalidStorageKeyError("Storage key contains an invalid character")

        if key.startswith("/"):
            raise InvalidStorageKeyError("Absolute storage keys are not allowed")

        parts = tuple(key.split("/"))

        if any(part in ("", ".", "..") for part in parts):
            raise InvalidStorageKeyError(
                "Storage key contains an invalid path component"
            )

        return parts

    def _resolve_path(
        self,
        key: str,
        *,
        create_parents: bool = False,
    ) -> Path:
        parts = self._validate_key(key)
        current = self._root

        for part in parts[:-1]:
            current = current / part

            try:
                if create_parents:
                    current.mkdir(exist_ok=True)

                if current.is_symlink():
                    raise StorageIOError(f"Symlink directory is not allowed: {current}")

                if current.exists() and not current.is_dir():
                    raise StorageIOError(
                        f"Storage path component is not a directory: {current}"
                    )

            except StorageIOError:
                raise
            except OSError as exc:
                raise StorageIOError(
                    f"Cannot access storage directory: {current}"
                ) from exc

        target = current / parts[-1]

        try:
            if target.is_symlink():
                raise StorageIOError(f"Symlink storage object is not allowed: {target}")
        except OSError as exc:
            raise StorageIOError(f"Cannot inspect storage object: {target}") from exc

        return target

    @staticmethod
    def _sha256(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def _describe_existing(self, key: str, path: Path) -> StorageObject:
        try:
            if path.is_symlink():
                raise StorageIOError(f"Symlink storage object is not allowed: {path}")

            metadata = path.stat()

            if not stat.S_ISREG(metadata.st_mode):
                raise StorageIOError(f"Storage object is not a regular file: {path}")

            data = path.read_bytes()

            return StorageObject(
                key=key,
                size=len(data),
                sha256=self._sha256(data),
            )

        except StorageIOError:
            raise
        except FileNotFoundError as exc:
            raise StorageNotFoundError(f"Storage object does not exist: {key}") from exc
        except OSError as exc:
            raise StorageIOError(f"Cannot inspect storage object: {key}") from exc

    def put(self, key: str, data: bytes) -> StorageObject:
        if not isinstance(data, bytes):
            raise TypeError("Storage data must be bytes")

        target = self._resolve_path(key, create_parents=True)
        digest = self._sha256(data)

        # Fast path: an existing object may already contain the same data.
        if target.exists():
            existing = self._describe_existing(key, target)

            if existing.sha256 == digest and existing.size == len(data):
                return existing

            raise StorageConflictError(
                f"Storage key already contains different data: {key}"
            )

        temp_path: Path | None = None

        try:
            fd, temp_name = tempfile.mkstemp(
                prefix=".tmp-",
                dir=target.parent,
            )
            temp_path = Path(temp_name)

            with os.fdopen(fd, "wb") as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())

            try:
                # Atomic no-clobber commit.
                # The temporary file and target are on the same filesystem.
                os.link(temp_path, target)

            except FileExistsError:
                # Another writer may have committed the same key.
                existing = self._describe_existing(key, target)

                if existing.sha256 == digest and existing.size == len(data):
                    return existing

                raise StorageConflictError(
                    f"Storage key already contains different data: {key}"
                )

            return StorageObject(
                key=key,
                size=len(data),
                sha256=digest,
            )

        except (StorageConflictError, StorageIOError):
            raise
        except OSError as exc:
            raise StorageIOError(f"Cannot store object: {key}") from exc
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    # A leftover temporary file does not invalidate
                    # an already committed object.
                    pass

    def get(self, key: str) -> bytes:
        path = self._resolve_path(key)

        try:
            if path.is_symlink():
                raise StorageIOError(f"Symlink storage object is not allowed: {path}")

            metadata = path.stat()

            if not stat.S_ISREG(metadata.st_mode):
                raise StorageIOError(f"Storage object is not a regular file: {path}")

            return path.read_bytes()

        except StorageIOError:
            raise
        except FileNotFoundError as exc:
            raise StorageNotFoundError(f"Storage object does not exist: {key}") from exc
        except OSError as exc:
            raise StorageIOError(f"Cannot read storage object: {key}") from exc

    def exists(self, key: str) -> bool:
        path = self._resolve_path(key)

        try:
            if path.is_symlink():
                raise StorageIOError(f"Symlink storage object is not allowed: {path}")

            if not path.exists():
                return False

            if not path.is_file():
                raise StorageIOError(f"Storage object is not a regular file: {key}")

            return True

        except StorageIOError:
            raise
        except OSError as exc:
            raise StorageIOError(f"Cannot check storage object: {key}") from exc

    def stat(self, key: str) -> StorageObject:
        path = self._resolve_path(key)
        return self._describe_existing(key, path)

    def delete(self, key: str) -> None:
        path = self._resolve_path(key)

        try:
            if path.is_symlink():
                raise StorageIOError(f"Symlink storage object is not allowed: {path}")

            if not path.exists():
                raise StorageNotFoundError(f"Storage object does not exist: {key}")

            if not path.is_file():
                raise StorageIOError(f"Storage object is not a regular file: {key}")

            path.unlink()

        except (StorageNotFoundError, StorageIOError):
            raise
        except FileNotFoundError as exc:
            raise StorageNotFoundError(f"Storage object does not exist: {key}") from exc
        except OSError as exc:
            raise StorageIOError(f"Cannot delete storage object: {key}") from exc
