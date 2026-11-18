"""Parse self-describing encrypted records from a GenerationPayload."""

from __future__ import annotations

from engram.processing.artifacts import (
    EncryptedChunkRecord,
    GenerationPayload,
    PayloadRecord,
)
from engram.processing.exceptions import PayloadError, UnsupportedPayloadVersionError

from .format import PAYLOAD_FORMAT_VERSION, decode_record


class BinaryPayloadParser:
    """Recover transport records; logical hashes are resolved by metadata separately."""

    def parse_record(
        self, payload: GenerationPayload, record: PayloadRecord
    ) -> EncryptedChunkRecord:
        if not isinstance(payload, GenerationPayload):
            raise PayloadError("payload must be a GenerationPayload artifact")
        if not isinstance(record, PayloadRecord):
            raise PayloadError("record must be a PayloadRecord artifact")
        if payload.format_version != PAYLOAD_FORMAT_VERSION:
            raise UnsupportedPayloadVersionError(
                f"Unsupported payload format version: {payload.format_version}"
            )
        if record not in payload.records:
            raise PayloadError("record does not belong to this payload")
        end = record.offset + record.length
        if record.offset < 0 or record.length <= 0 or end > len(payload.data):
            raise PayloadError("record range is outside payload data")

        encoded = payload.data[record.offset : end]
        chunk = decode_record(encoded, expected_format_version=payload.format_version)
        if chunk.object_id != record.object_id or chunk.chunk_id != record.chunk_id:
            raise PayloadError("Record identity does not match PayloadRecord metadata")
        return chunk

    def parse_all(self, payload: GenerationPayload) -> tuple[EncryptedChunkRecord, ...]:
        if not isinstance(payload, GenerationPayload):
            raise PayloadError("payload must be a GenerationPayload artifact")
        return tuple(self.parse_record(payload, record) for record in payload.records)
