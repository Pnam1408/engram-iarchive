"""Generation payload and logical record-location artifacts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ._validation import (
    require_bytes,
    require_non_empty_string,
    require_non_negative_int,
    require_positive_int,
    require_sha256,
)
from engram.processing.exceptions import InvalidArtifactError


@dataclass(frozen=True, slots=True)
class PayloadRecord:
    record_index: int
    object_id: str
    chunk_id: str
    offset: int
    length: int

    def __post_init__(self) -> None:
        require_non_negative_int("record_index", self.record_index)
        require_non_empty_string("object_id", self.object_id)
        require_non_empty_string("chunk_id", self.chunk_id)
        require_non_negative_int("offset", self.offset)
        require_positive_int("length", self.length)


@dataclass(frozen=True, slots=True)
class GenerationPayload:
    data: bytes
    records: tuple[PayloadRecord, ...]
    format_version: int
    sha256: str

    def __post_init__(self) -> None:
        require_bytes("data", self.data)
        require_non_negative_int("format_version", self.format_version)
        if self.format_version == 0:
            raise InvalidArtifactError("format_version must be >= 1")
        require_sha256("sha256", self.sha256)
        if hashlib.sha256(self.data).hexdigest() != self.sha256:
            raise InvalidArtifactError("sha256 does not match payload data")
        if not isinstance(self.records, tuple):
            raise InvalidArtifactError("records must be a tuple")
        if any(not isinstance(record, PayloadRecord) for record in self.records):
            raise InvalidArtifactError("records must contain PayloadRecord values")
        if tuple(record.record_index for record in self.records) != tuple(range(len(self.records))):
            raise InvalidArtifactError("record_index values must be contiguous from zero")

        identities = [(record.object_id, record.chunk_id) for record in self.records]
        if len(identities) != len(set(identities)):
            raise InvalidArtifactError("a logical chunk may appear only once per generation")

        expected_offset = 0
        for record in self.records:
            if record.offset != expected_offset:
                raise InvalidArtifactError(
                    "payload records must be ordered, contiguous, and non-overlapping"
                )
            expected_offset += record.length
            if expected_offset > len(self.data):
                raise InvalidArtifactError("payload record exceeds payload data")

        if expected_offset != len(self.data):
            if self.records or self.data:
                raise InvalidArtifactError("records must cover the complete payload data")
