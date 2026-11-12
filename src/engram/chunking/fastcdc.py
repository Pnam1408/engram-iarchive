"""FastCDC v2020 chunking backed by the pinned fastcdc-rs crate."""
from __future__ import annotations

from ._chunks import build_chunks
from .config import FastCDCConfig
from .types import Chunk


class FastCDCChunker:
    """Content-defined chunker using the Rust ``fastcdc`` crate.

    The extension is deliberately required: this class never silently falls
    back to a different Python implementation because that would make
    benchmark results and chunk boundaries implementation-dependent.
    """

    algorithm = "fastcdc-rs-v2020"
    implementation = "rust"

    def __init__(self, config: FastCDCConfig | None = None) -> None:
        self.config = config or FastCDCConfig()

    def chunk(self, data: bytes) -> tuple[Chunk, ...]:
        if not isinstance(data, bytes):
            raise TypeError("data must be bytes")
        if not data:
            return ()

        try:
            from . import _fastcdc
        except ImportError as exc:
            raise RuntimeError(
                "FastCDC native extension is not installed. Build it with "
                "'maturin develop --manifest-path native/fastcdc/Cargo.toml' "
                "from the repository root; no fallback implementation is used."
            ) from exc

        boundaries = _fastcdc.chunk_boundaries(
            data,
            self.config.min_size,
            self.config.avg_size,
            self.config.max_size,
        )
        return build_chunks(data, boundaries)
