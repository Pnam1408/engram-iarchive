"""Helpers for turning native boundaries into immutable chunk records."""
from __future__ import annotations

import hashlib
from collections.abc import Iterable

from .types import Chunk


def build_chunks(data: bytes, boundaries: Iterable[tuple[int, int]]) -> tuple[Chunk, ...]:
    """Validate boundaries and materialize chunks with SHA-256 digests."""
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")

    chunks: list[Chunk] = []
    expected_offset = 0
    for index, (offset, size) in enumerate(boundaries):
        if not isinstance(offset, int) or not isinstance(size, int):
            raise TypeError("chunk boundaries must contain integer offset/size pairs")
        if size <= 0 or offset != expected_offset:
            raise ValueError("chunk boundaries must be positive, contiguous, and ordered")
        end = offset + size
        if end > len(data):
            raise ValueError("chunk boundary exceeds input length")
        payload = data[offset:end]
        chunks.append(
            Chunk(
                index=index,
                offset=offset,
                size=size,
                sha256=hashlib.sha256(payload).hexdigest(),
                data=payload,
            )
        )
        expected_offset = end

    if expected_offset != len(data):
        if data:
            raise ValueError("chunk boundaries do not cover the complete input")
        if chunks:
            raise ValueError("empty input cannot contain chunks")
    return tuple(chunks)
