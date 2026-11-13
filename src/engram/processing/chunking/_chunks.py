"""Internal helpers for materializing validated Chunk artifacts."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from engram.processing.artifacts import Chunk
from engram.processing.exceptions import ChunkingError


def build_chunks(
    data: bytes,
    boundaries: Iterable[tuple[int, int]],
) -> tuple[Chunk, ...]:
    """Build chunks from ``(offset, size)`` boundaries.

    Boundaries must be positive, ordered, contiguous, non-overlapping, and
    cover the complete input. Empty input has no chunks.
    """
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")

    chunks: list[Chunk] = []
    expected_offset = 0

    try:
        for index, boundary in enumerate(boundaries):
            if (
                not isinstance(boundary, (tuple, list))
                or len(boundary) != 2
            ):
                raise ChunkingError("each boundary must be an (offset, size) pair")

            offset, size = boundary
            if (
                not isinstance(offset, int)
                or isinstance(offset, bool)
                or not isinstance(size, int)
                or isinstance(size, bool)
            ):
                raise ChunkingError("boundary offset and size must be integers")
            if size <= 0 or offset != expected_offset:
                raise ChunkingError(
                    "boundaries must be positive, contiguous, and ordered"
                )

            end = offset + size
            if end > len(data):
                raise ChunkingError("boundary exceeds input length")

            chunk_data = data[offset:end]
            chunks.append(
                Chunk(
                    index=index,
                    offset=offset,
                    size=size,
                    sha256=hashlib.sha256(chunk_data).hexdigest(),
                    data=chunk_data,
                )
            )
            expected_offset = end
    except ChunkingError:
        raise
    except Exception as exc:
        raise ChunkingError("could not materialize chunk boundaries") from exc

    if expected_offset != len(data):
        if data:
            raise ChunkingError("boundaries do not cover the complete input")
        if chunks:
            raise ChunkingError("empty input cannot contain chunks")

    return tuple(chunks)
