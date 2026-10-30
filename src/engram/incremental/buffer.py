#engram/incremental/buffer.py
"""Short-lived generation buffer and seal-staging payload files.

Buffer bytes are kept outside the long-lived JSON object metadata. The metadata
records the committed buffer length so recovery can discard bytes written just
before a crash but not yet reflected in the metadata.
"""
from __future__ import annotations

from pathlib import Path

from .state import StateMixin


class BufferMixin(StateMixin):
    def _buffer_path(self, object_id: str) -> Path:
        return self.buffer_dir / f"{self._object_key(object_id)}.buffer"

    def _staging_payload_path(self, object_id: str) -> Path:
        return self.staging_dir / f"{self._object_key(object_id)}.ciphertext"

    def _read_buffer(self, object_id: str, state: dict) -> bytes:
        """Read only the buffer bytes acknowledged by the metadata."""
        path = self._buffer_path(object_id)
        expected_size = int(state["open"].get("buffer_size", 0))
        if not path.exists():
            if expected_size == 0:
                return b""
            raise ValueError(f"Open buffer is missing for object {object_id!r}")

        data = path.read_bytes()
        if len(data) < expected_size:
            raise ValueError(
                f"Open buffer is shorter than metadata for object {object_id!r}: "
                f"expected {expected_size}, found {len(data)}"
            )
        if len(data) > expected_size:
            # A crash may occur after replacing the buffer file but before saving
            # the new metadata. Ignore the uncommitted tail deterministically.
            data = data[:expected_size]
            self._atomic_write(path, data)
        return data

    def _write_buffer(self, object_id: str, data: bytes) -> None:
        self._atomic_write(self._buffer_path(object_id), data)

    def _write_staging_payload(self, object_id: str, ciphertext: bytes) -> None:
        self._atomic_write(self._staging_payload_path(object_id), ciphertext)

    def _read_staging_payload(self, object_id: str) -> bytes:
        path = self._staging_payload_path(object_id)
        try:
            return path.read_bytes()
        except OSError as exc:
            raise ValueError(f"Staged ciphertext is missing for object {object_id!r}") from exc

    @staticmethod
    def _remove_if_exists(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # Cleanup is best-effort. Recovery retries it after the committed
            # state says the buffer/staging payload is no longer needed.
            pass

    def _cleanup_committed_temporary_files(self, object_id: str, state: dict) -> None:
        """Remove temporary files only when metadata says no work is pending."""
        if not state.get("sealing") and not state["open"].get("entries"):
            self._remove_if_exists(self._buffer_path(object_id))
            self._remove_if_exists(self._staging_payload_path(object_id))
