from enum import Enum


class ColumnFamily(str, Enum):
    OBJECTS = "objects"
    VERSIONS = "versions"
    VERSION_CHUNKS = "version_chunks"
    CHUNKS = "chunks"
    GENERATIONS = "generations"
    GENERATION_CHUNKS = "generation_chunks"
    CHUNK_REFS = "chunk_refs"
    SHARDS = "shards"
    MMR = "mmr"
    SYSTEM = "system"


COLUMN_FAMILIES = tuple(cf.value for cf in ColumnFamily)
