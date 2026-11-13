"""Chunk artifact shared by all chunking strategies and later stages."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ._validation import require_non_negative_int, require_positive_int, require_bytes, require_sha256


@dataclass(frozen=True, slots=True)
class Chunk:
    """A plaintext byte range produced by a chunking operation.

    ``index`` is local to one chunking result. It is deliberately not a logical
    ``chunk_id``; logical identity is assigned by the storage coordinator.
    """

    index: int
    offset: int
    size: int
    sha256: str
    data: bytes

    def __post_init__(self) -> None:
        require_non_negative_int("index", self.index)
        require_non_negative_int("offset", self.offset)
        require_positive_int("size", self.size)
        require_bytes("data", self.data)
        require_sha256("sha256", self.sha256)
        if len(self.data) != self.size:
            from engram.processing.exceptions import InvalidArtifactError
            raise InvalidArtifactError("size must equal len(data)")
        if hashlib.sha256(self.data).hexdigest() != self.sha256:
            from engram.processing.exceptions import InvalidArtifactError
            raise InvalidArtifactError("sha256 does not match data")

    @property
    def end(self) -> int:
        """Exclusive end offset in the original input."""
        return self.offset + self.size
