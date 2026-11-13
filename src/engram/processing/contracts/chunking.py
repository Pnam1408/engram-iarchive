"""Chunking stage protocol."""

from typing import Protocol, runtime_checkable

from engram.processing.artifacts import Chunk


@runtime_checkable
class Chunker(Protocol):
    def chunk(self, data: bytes) -> tuple[Chunk, ...]: ...
