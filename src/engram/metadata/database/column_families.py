from enum import Enum


class ColumnFamily(str, Enum):
    # Logical Metadata
    OBJECTS = "objects"
    VERSIONS = "versions"
    VERSION_DELTAS = "version_deltas"
    CHUNKS = "chunks"
    CHUNK_DEDUP_INDEX = "chunk_dedup_index"
    OBJECT_CHUNK_INDEX = "object_chunk_index"
    CHECKPOINTS = "checkpoints"
    CHECKPOINT_ENTRIES = "checkpoint_entries"
    MMR = "mmr"
    MMR_NODES = "mmr_nodes"

    # Physical Metadata
    GENERATIONS = "generations"
    GENERATION_MANIFESTS = "generation_manifests"
    GENERATION_MANIFEST_ENTRIES = "generation_manifest_entries"
    GENERATION_SHARDS = "generation_shards"

    CHUNK_PLACEMENTS = "chunk_placements"
    CHUNK_PLACEMENT_INDEX = "chunk_placement_index"

    SHARD_PLACEMENTS = "shard_placements"
    SHARD_PLACEMENT_INDEX = "shard_placement_index"

    STORAGE_RECEIPTS = "storage_receipts"
    STORAGE_RECEIPT_INDEX = "storage_receipt_index"

    # Legacy Physical Metadata - retained during transition
    GENERATION_CHUNKS = "generation_chunks"
    SHARDS = "shards"

    # Shard Commitment Metadata
    GENERATION_SHARD_COMMITMENTS = "generation_shard_commitments"
    GENERATION_SHARD_COMMITMENT_ENTRIES = "generation_shard_commitment_entries"

    SYSTEM = "system"


COLUMN_FAMILIES = tuple(cf.value for cf in ColumnFamily)
