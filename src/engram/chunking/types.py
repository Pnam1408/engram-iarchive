"""Shared chunk metadata used by the chunking implementations."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Chunk:
    """A byte range produced by a chunker.

    ``data`` is retained so callers can immediately persist or hash the chunk.
    ``offset`` and ``size`` always refer to the original input byte string.
    """

    index: int
    offset: int
    size: int
    sha256: str
    data: bytes

    @property
    def end(self) -> int:
        """Exclusive end offset in the original input."""
        return self.offset + self.size
