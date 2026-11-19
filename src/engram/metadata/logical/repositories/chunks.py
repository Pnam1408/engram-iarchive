from ..models import Chunk
from ..models._validation import validate_uuid
from ...database import ColumnFamily, KeyCodec
from ...repositories import (
    BaseRepository,
    CorruptMetadataError,
    RecordAlreadyExistsError,
)
from ...database.serialization import encode_bytes, decode_bytes


class ChunkRepository(BaseRepository):
    """Immutable Chunk Catalog keyed by (ObjectID, ChunkID)."""

    _FIELDS = {
        "object_id",
        "chunk_id",
        "content_hash",
        "plaintext_size",
        "created_at",
    }

    @classmethod
    def _to_dict(cls, chunk: Chunk) -> dict:
        return {
            "object_id": chunk.object_id,
            "chunk_id": chunk.chunk_id,
            "content_hash": encode_bytes(chunk.content_hash),
            "plaintext_size": chunk.plaintext_size,
            "created_at": chunk.created_at,
        }

    @classmethod
    def _from_dict(cls, data: dict) -> Chunk:
        cls._require_fields(data, cls._FIELDS, "Chunk")
        return Chunk(
            object_id=data["object_id"],
            chunk_id=data["chunk_id"],
            content_hash=decode_bytes(data["content_hash"], "content_hash"),
            plaintext_size=data["plaintext_size"],
            created_at=data["created_at"],
        )

    def get(self, object_id: str, chunk_id: str) -> Chunk | None:
        self._require_non_empty_string(object_id, "object_id")
        validate_uuid(chunk_id, "chunk_id")
        key = KeyCodec.chunk_key(object_id, chunk_id)
        try:
            data = self._get(ColumnFamily.CHUNKS, key)
            if data is None:
                return None
            chunk = self._from_dict(data)
            if chunk.object_id != object_id or chunk.chunk_id != chunk_id:
                raise CorruptMetadataError("Chunk fields do not match storage key")
            return chunk
        except CorruptMetadataError:
            raise
        except Exception as exc:
            raise CorruptMetadataError("Cannot decode stored Chunk") from exc

    def exists(self, object_id: str, chunk_id: str) -> bool:
        self._require_non_empty_string(object_id, "object_id")
        validate_uuid(chunk_id, "chunk_id")
        return (
            self.db.get(
                ColumnFamily.CHUNKS,
                KeyCodec.chunk_key(object_id, chunk_id),
            )
            is not None
        )

    def put(self, chunk: Chunk, batch) -> None:
        if not isinstance(chunk, Chunk):
            raise TypeError("chunk must be a Chunk")

        key = KeyCodec.chunk_key(chunk.object_id, chunk.chunk_id)
        if self.db.get(ColumnFamily.CHUNKS, key) is not None:
            raise RecordAlreadyExistsError(
                f"Chunk {chunk.object_id!r}/{chunk.chunk_id!r} already exists"
            )

        self._put_batch(batch, ColumnFamily.CHUNKS, key, self._to_dict(chunk))
