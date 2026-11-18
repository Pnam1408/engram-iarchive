"""Generation payload assembly and parsing protocols."""

from typing import Protocol

from engram.processing.artifacts import (
    EncryptedChunk,
    EncryptedChunkRecord,
    GenerationPayload,
    PayloadRecord,
)


class PayloadBuilder(Protocol):
    def build(self, chunks: tuple[EncryptedChunk, ...]) -> GenerationPayload: ...


class PayloadParser(Protocol):
    def parse_record(
        self, payload: GenerationPayload, record: PayloadRecord
    ) -> EncryptedChunkRecord: ...
