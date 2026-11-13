"""FastCDC v2020 strategy backed by the project's pinned Rust extension."""

from __future__ import annotations

from importlib import import_module
from types import ModuleType

from engram.processing.artifacts import Chunk
from engram.processing.exceptions import ChunkingError

from ._chunks import build_chunks
from .config import FastCDCConfig


class FastCDCChunker:
    """Content-defined chunker using ``fastcdc-rs`` (FastCDC v2020).

    The Rust extension is reused without depending on the legacy Python
    chunker implementation. In the current repository build it is installed
    as ``engram.chunking._fastcdc``; this import bridge can be changed when
    the native crate is relocated in a later migration step.

    There is intentionally no Python fallback: benchmark boundaries must
    come from the same pinned native implementation.
    """

    algorithm = "fastcdc-rs-v2020"
    implementation = "rust"

    def __init__(self, config: FastCDCConfig | None = None) -> None:
        self.config = config if config is not None else FastCDCConfig()

    @staticmethod
    def _load_native_extension() -> ModuleType:
        try:
            return import_module("engram.processing.chunking._fastcdc")
        except ImportError as exc:
            raise ChunkingError(
                "FastCDC native extension is unavailable. Build the existing "
                "fastcdc Rust extension with maturin; no fallback is used."
            ) from exc

    def chunk(self, data: bytes) -> tuple[Chunk, ...]:
        if not isinstance(data, bytes):
            raise TypeError("data must be bytes")
        if not data:
            return ()

        native = self._load_native_extension()
        try:
            boundaries = native.chunk_boundaries(
                data,
                self.config.min_size,
                self.config.avg_size,
                self.config.max_size,
            )
        except Exception as exc:
            raise ChunkingError("FastCDC native chunking failed") from exc

        return build_chunks(data, boundaries)
