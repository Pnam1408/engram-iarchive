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

    # Physical Metadata - retained during transition
    GENERATIONS = "generations"
    GENERATION_CHUNKS = "generation_chunks"
    SHARDS = "shards"

    SYSTEM = "system"


COLUMN_FAMILIES = tuple(cf.value for cf in ColumnFamily)
