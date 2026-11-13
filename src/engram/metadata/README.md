# ENGRAM Metadata Layer

## Responsibilities

This package stores logical metadata and physical-location indexes in RocksDB.
Ciphertext, generation payloads, and shard bytes remain in the filesystem.

Column Families:
- `objects`: logical object identity and latest version.
- `versions`: authoritative ordered version manifests.
- `version_chunks`: ordered lookup index derived atomically from manifests.
- `chunks`: per-object chunk catalog and physical assignment.
- `generations`: global generation metadata; generations are not owned by objects.
- `generation_chunks`: ordered record index inside generation payloads.
- `chunk_refs`: count of references from version manifests.
- `shards`: physical shard metadata.
- `mmr`, `system`: reserved for later MMR/system metadata.

## Lifecycle invariants

Object: `ACTIVE -> DELETING`.

Chunk: `PENDING -> ASSIGNED -> AVAILABLE`.
A chunk is assigned to exactly one generation. Its status and physical location
are changed by `GenerationRepository`, not by arbitrary metadata updates.

Generation: `OPEN -> SEALING -> STORED -> AVAILABLE`.
- `OPEN`: accepts records.
- `SEALING`: shard metadata may be registered.
- `STORED`: metadata contains at least `data_shards` distinct, in-range shard
  indexes with required location fields.
- `AVAILABLE`: chunks become retrievable through their generation record
  locations.

**Important:** Repository-level STORED checks validate metadata only. The
Generation Manager must verify actual files, checksums, and reconstructibility
before asking the repository to transition to STORED. RocksDB cannot atomically
commit filesystem operations.

## Version invariants

`versions` is the authoritative manifest. `version_chunks` is a derived index.
Both are written in one RocksDB WriteBatch and `get_chunks()` checks they agree.
`chunk_refs.reference_count` counts occurrences in all manifests; repeated
occurrences of the same chunk in one manifest count separately.

A version may reference only AVAILABLE chunks belonging to the same object.

## Concurrency contract

The current repository API uses read/validate/write sequences and therefore
assumes **one metadata writer at a time**. A RocksDB WriteBatch makes its writes
atomic, but does not make the preceding reads serializable. Do not run multiple
independent writers/processes against the same logical metadata workflow until
a transaction/locking strategy is introduced.

## Durability and recovery

Filesystem publication and RocksDB metadata publication are separate failure
domains. The Generation Manager should use staging, verify files, atomically
publish the generation directory where supported, and then commit metadata.
On restart it must reconcile incomplete staging/published directories with
generation states. Metadata repositories do not perform filesystem recovery.
