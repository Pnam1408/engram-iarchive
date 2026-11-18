# Generation payload format

`BinaryPayloadBuilder` packs a tuple of `EncryptedChunk` artifacts into a
`GenerationPayload`. The builder serializes encryption metadata and ciphertext,
but deliberately omits the plaintext SHA-256 digest. That digest belongs to
the logical Chunk Catalog and is supplied to the decryptor after parsing.

## V2 binary layout

All integer fields are unsigned big-endian.

| Field | Encoding |
|---|---|
| Magic | 4 bytes: `ENGR` |
| Format version | u16 (`2`) |
| Header length | u32; includes prefix and all header fields, excludes ciphertext |
| Object ID | u32 byte length + UTF-8 bytes |
| Chunk ID | u32 byte length + UTF-8 bytes |
| Algorithm | u16 byte length + UTF-8 bytes |
| Key ID | u16 byte length + UTF-8 bytes |
| AAD version | u16 |
| Plaintext size | u64 |
| Nonce | u16 byte length + bytes |
| Ciphertext length | u64 |
| Ciphertext | exact number of bytes declared above |

The GCM authentication tag is part of `ciphertext`. Secret key material and
plaintext hashes are not stored in the record. `EncryptedChunkRecord` is the
parsed transport artifact; it is not a substitute for logical chunk metadata.

`PayloadRecord.offset` points to the first byte of the record (the magic), and
`PayloadRecord.length` includes header and ciphertext. Records are packed
contiguously in input order. `GenerationPayload.sha256` covers all concatenated
payload bytes.

## API

```python
payload = BinaryPayloadBuilder().build(encrypted_chunks)
records = BinaryPayloadParser().parse_all(payload)
plaintext = AESGCMDecryptor().decrypt(
    records[0], key=key, expected_plaintext_sha256=chunk_catalog.content_hash
)
```

An empty tuple produces an empty version-2 payload with no records. V2 is the
only supported format in this simulation; unknown versions fail with
`UnsupportedPayloadVersionError`.
