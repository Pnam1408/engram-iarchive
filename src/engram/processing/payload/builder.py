"""Build a packed GenerationPayload from encrypted chunks."""

from __future__ import annotations

import hashlib

from engram.processing.artifacts import EncryptedChunk, GenerationPayload, PayloadRecord
from engram.processing.exceptions import InvalidArtifactError, PayloadError

from .format import PAYLOAD_FORMAT_VERSION, encode_record


class BinaryPayloadBuilder:
    """Serialize encrypted chunks as contiguous records without plaintext digests."""

    def build(self, chunks: tuple[EncryptedChunk, ...]) -> GenerationPayload:
        if not isinstance(chunks, tuple):
            raise PayloadError("chunks must be a tuple")
        if any(not isinstance(chunk, EncryptedChunk) for chunk in chunks):
            raise PayloadError("chunks must contain only EncryptedChunk artifacts")

        identities = [(chunk.object_id, chunk.chunk_id) for chunk in chunks]
        if len(identities) != len(set(identities)):
            raise PayloadError("a logical chunk may appear only once per generation")

        payload_parts: list[bytes] = []
        records: list[PayloadRecord] = []
        offset = 0
        for index, chunk in enumerate(chunks):
            try:
                encoded = encode_record(chunk)
            except PayloadError:
                raise
            except (TypeError, ValueError, OverflowError) as exc:
                raise PayloadError("Failed to encode encrypted chunk") from exc
            payload_parts.append(encoded)
            records.append(
                PayloadRecord(
                    record_index=index,
                    object_id=chunk.object_id,
                    chunk_id=chunk.chunk_id,
                    offset=offset,
                    length=len(encoded),
                )
            )
            offset += len(encoded)

        data = b"".join(payload_parts)
        return GenerationPayload(
            data=data,
            records=tuple(records),
            format_version=PAYLOAD_FORMAT_VERSION,
            sha256=hashlib.sha256(data).hexdigest(),
        )
