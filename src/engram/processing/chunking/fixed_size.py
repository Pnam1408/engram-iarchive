"""Fixed-size chunking strategy used as the deterministic baseline."""

from __future__ import annotations

from engram.processing.artifacts import Chunk
from engram.processing.exceptions import InvalidConfigurationError

from ._chunks import build_chunks


class FixedSizeChunker:
    """Partition bytes into fixed-size chunks and a final remainder."""

    algorithm = "fixed-size-v1"

    def __init__(self, chunk_size: int = 4 * 1024) -> None:
        if not isinstance(chunk_size, int) or isinstance(chunk_size, bool):
            raise InvalidConfigurationError("chunk_size must be an integer")
        if chunk_size <= 0:
            raise InvalidConfigurationError("chunk_size must be positive")
        self.chunk_size = chunk_size

    def chunk(self, data: bytes) -> tuple[Chunk, ...]:
        if not isinstance(data, bytes):
            raise TypeError("data must be bytes")

        boundaries = (
            (offset, min(self.chunk_size, len(data) - offset))
            for offset in range(0, len(data), self.chunk_size)
        )
        return build_chunks(data, boundaries)
