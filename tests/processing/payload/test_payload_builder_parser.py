from __future__ import annotations

import hashlib

import pytest

from engram.processing.artifacts import Chunk, EncryptedChunk, GenerationPayload
from engram.processing.encryption import AESGCMDecryptor, AESGCMEncryptor, generate_key
from engram.processing.exceptions import (
    PayloadError,
    UnsupportedPayloadVersionError,
)
from engram.processing.payload import (
    BinaryPayloadBuilder,
    BinaryPayloadParser,
    PAYLOAD_FORMAT_VERSION,
)


@pytest.fixture
def encrypted_chunks():
    encryptor = AESGCMEncryptor()
    key = generate_key()
    inputs = (
        ("object-A", "chunk-001", b"alpha\\x00payload"),
        ("object-B", "chunk-002", b"second object data"),
        ("object-A", "chunk-003", b"another chunk"),
    )
    result = tuple(
        encryptor.encrypt(
            Chunk(
                index=index,
                offset=sum(len(item[2]) for item in inputs[:index]),
                size=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                data=data,
            ),
            object_id=object_id,
            chunk_id=chunk_id,
            key=key,
            key_id="test-key-v1",
        )
        for index, (object_id, chunk_id, data) in enumerate(inputs)
    )
    return key, inputs, result


def test_build_and_parse_all_round_trip(encrypted_chunks):
    key, inputs, chunks = encrypted_chunks
    payload = BinaryPayloadBuilder().build(chunks)
    parser = BinaryPayloadParser()

    recovered = parser.parse_all(payload)
    assert recovered == chunks

    decryptor = AESGCMDecryptor()
    plaintexts = tuple(decryptor.decrypt(item, key=key) for item in recovered)
    assert plaintexts == tuple(item[2] for item in inputs)


def test_records_are_contiguous_and_cover_payload(encrypted_chunks):
    _, _, chunks = encrypted_chunks
    payload = BinaryPayloadBuilder().build(chunks)

    assert payload.format_version == PAYLOAD_FORMAT_VERSION
    assert payload.records[0].offset == 0
    for left, right in zip(payload.records, payload.records[1:]):
        assert left.offset + left.length == right.offset
    assert payload.records[-1].offset + payload.records[-1].length == len(payload.data)


def test_parse_one_record_independently(encrypted_chunks):
    _, _, chunks = encrypted_chunks
    payload = BinaryPayloadBuilder().build(chunks)
    parser = BinaryPayloadParser()

    assert parser.parse_record(payload, payload.records[1]) == chunks[1]


def test_empty_generation_payload():
    payload = BinaryPayloadBuilder().build(())
    assert payload.data == b""
    assert payload.records == ()
    assert BinaryPayloadParser().parse_all(payload) == ()


def test_builder_rejects_duplicate_logical_chunk(encrypted_chunks):
    _, _, chunks = encrypted_chunks
    with pytest.raises(PayloadError, match="only once"):
        BinaryPayloadBuilder().build((chunks[0], chunks[0]))


def test_builder_requires_tuple(encrypted_chunks):
    _, _, chunks = encrypted_chunks
    with pytest.raises(PayloadError, match="tuple"):
        BinaryPayloadBuilder().build(list(chunks))


def test_parser_rejects_record_from_another_payload(encrypted_chunks):
    _, _, chunks = encrypted_chunks
    first = BinaryPayloadBuilder().build((chunks[0],))
    second = BinaryPayloadBuilder().build((chunks[1],))

    with pytest.raises(PayloadError, match="does not belong"):
        BinaryPayloadParser().parse_record(first, second.records[0])


def test_parser_rejects_unsupported_payload_version(encrypted_chunks):
    _, _, chunks = encrypted_chunks
    payload = BinaryPayloadBuilder().build((chunks[0],))
    unsupported = GenerationPayload(
        data=payload.data,
        records=payload.records,
        format_version=PAYLOAD_FORMAT_VERSION + 1,
        sha256=payload.sha256,
    )
    with pytest.raises(UnsupportedPayloadVersionError):
        BinaryPayloadParser().parse_all(unsupported)


def test_parser_detects_record_identity_mismatch(encrypted_chunks):
    _, _, chunks = encrypted_chunks
    payload = BinaryPayloadBuilder().build((chunks[0],))
    record = payload.records[0]
    forged = type(record)(
        record_index=record.record_index,
        object_id="other-object",
        chunk_id=record.chunk_id,
        offset=record.offset,
        length=record.length,
    )
    # A record not present in the payload metadata is rejected before parsing.
    with pytest.raises(PayloadError):
        BinaryPayloadParser().parse_record(payload, forged)


def test_parser_rejects_truncated_record():
    from engram.processing.payload.format import decode_record

    with pytest.raises(PayloadError):
        decode_record(b"ENGR\\x00", expected_format_version=PAYLOAD_FORMAT_VERSION)


def test_parse_record_preserves_all_encryption_metadata(encrypted_chunks):
    _, _, chunks = encrypted_chunks
    payload = BinaryPayloadBuilder().build(chunks)
    parsed = BinaryPayloadParser().parse_all(payload)

    for original, decoded in zip(chunks, parsed):
        assert decoded.object_id == original.object_id
        assert decoded.chunk_id == original.chunk_id
        assert decoded.algorithm == original.algorithm
        assert decoded.key_id == original.key_id
        assert decoded.nonce == original.nonce
        assert decoded.ciphertext == original.ciphertext
        assert decoded.plaintext_size == original.plaintext_size
        assert decoded.plaintext_sha256 == original.plaintext_sha256
        assert decoded.aad_version == original.aad_version
