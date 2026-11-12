"""Fixed-size chunking retained as the experimental baseline."""

from __future__ import annotations

from ._chunks import build_chunks
from .types import Chunk


class FixedSizeChunker:
    """Split data into fixed-size chunks, except for the final remainder."""

    def __init__(self, chunk_size: int = 4 * 1024) -> None:
        if not isinstance(chunk_size, int) or isinstance(chunk_size, bool):
            raise TypeError("chunk_size must be an integer")
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        self.chunk_size = chunk_size

    def chunk(self, data: bytes) -> tuple[Chunk, ...]:
        if not isinstance(data, bytes):
            raise TypeError("data must be bytes")
        boundaries = (
            (offset, min(self.chunk_size, len(data) - offset))
            for offset in range(0, len(data), self.chunk_size)
        )
        return build_chunks(data, boundaries)
