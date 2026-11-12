from .objects import ObjectRepository
from .versions import VersionRepository
from .chunks import ChunkRepository
from .generations import GenerationRepository
from .shards import ShardRepository


class MetadataRepositories:
    """Convenience container for all metadata repositories."""

    def __init__(self, db):
        self.objects = ObjectRepository(db)
        self.versions = VersionRepository(db)
        self.chunks = ChunkRepository(db)
        self.generations = GenerationRepository(db)
        self.shards = ShardRepository(db)


__all__ = [
    "MetadataRepositories",
    "ObjectRepository",
    "VersionRepository",
    "ChunkRepository",
    "GenerationRepository",
    "ShardRepository",
]
