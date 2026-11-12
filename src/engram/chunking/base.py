"""Structural interface shared by chunking strategies."""
from __future__ import annotations

from typing import Protocol

from .types import Chunk


class Chunker(Protocol):
    """A strategy that partitions bytes into ordered, contiguous chunks."""

    def chunk(self, data: bytes) -> tuple[Chunk, ...]:
        """Partition ``data`` without changing its contents."""
        ...
