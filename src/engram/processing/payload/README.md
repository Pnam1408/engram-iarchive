# Generation payload format

`BinaryPayloadBuilder` packs a tuple of `EncryptedChunk` artifacts into one
`GenerationPayload`. Each record is self-describing, so
`BinaryPayloadParser` can reconstruct the complete encrypted artifact without
reading RocksDB.

## V1 binary layout

All integer fields are unsigned big-endian.

| Field | Encoding |
|---|---|
| Magic | 4 bytes: `ENGR` |
| Format version | u16 |
| Header length | u32; includes prefix and all header fields |
| Object ID | u32 byte length + UTF-8 bytes |
| Chunk ID | u32 byte length + UTF-8 bytes |
| Algorithm | u16 byte length + UTF-8 bytes |
| Key ID | u16 byte length + UTF-8 bytes |
| AAD version | u16 |
| Plaintext size | u64 |
| Plaintext SHA-256 | 32 raw bytes |
| Nonce | u16 byte length + bytes |
| Ciphertext length | u64 |
| Ciphertext | exact number of bytes declared above |

The GCM authentication tag is already part of `EncryptedChunk.ciphertext`.
The record header stores metadata only; it does not store secret key material.

`PayloadRecord.offset` points to the first byte of the record (the magic),
and `PayloadRecord.length` includes header and ciphertext. Records are packed
contiguously in input order. `GenerationPayload.sha256` covers the complete
concatenated payload bytes.

## Compatibility

V1 is a new format and does not reinterpret historical generation payloads.
A future format must use a new version and a parser implementation that
explicitly supports it. Unknown versions fail with
`UnsupportedPayloadVersionError`.

## API

```python
builder = BinaryPayloadBuilder()
payload = builder.build(encrypted_chunks)

parser = BinaryPayloadParser()
encrypted_chunks = parser.parse_all(payload)
one_chunk = parser.parse_record(payload, payload.records[0])
```

An empty tuple produces an empty version-1 payload with no records.
