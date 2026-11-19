from ..models._validation import validate_uuid
from ...database import ColumnFamily, KeyCodec
from ...repositories import (
    BaseRepository,
    CorruptMetadataError,
    RecordAlreadyExistsError,
)


class ChunkDedupIndexRepository(BaseRepository):
    """Secondary index from (ObjectID, hash, size) to ChunkID."""

    _FIELDS = {"chunk_id"}

    @staticmethod
    def _validate_lookup(
        object_id: str,
        content_hash: bytes,
        plaintext_size: int,
    ) -> bytes:
        if not isinstance(object_id, str) or not object_id.strip():
            raise ValueError("object_id must be a non-empty string")
        if not isinstance(content_hash, bytes) or len(content_hash) != 32:
            raise ValueError("content_hash must contain exactly 32 bytes")
        if (
            isinstance(plaintext_size, bool)
            or not isinstance(plaintext_size, int)
            or plaintext_size <= 0
        ):
            raise ValueError("plaintext_size must be a positive integer")
        return KeyCodec.chunk_dedup_key(
            object_id,
            content_hash,
            plaintext_size,
        )

    def get(
        self,
        object_id: str,
        content_hash: bytes,
        plaintext_size: int,
    ) -> str | None:
        key = self._validate_lookup(object_id, content_hash, plaintext_size)
        try:
            data = self._get(ColumnFamily.CHUNK_DEDUP_INDEX, key)
            if data is None:
                return None
            self._require_fields(data, self._FIELDS, "ChunkDedupIndex")
            chunk_id = data["chunk_id"]
            validate_uuid(chunk_id, "chunk_id")
            return chunk_id
        except CorruptMetadataError:
            raise
        except Exception as exc:
            raise CorruptMetadataError(
                "Cannot decode stored ChunkDedupIndex mapping"
            ) from exc

    def put(
        self,
        object_id: str,
        content_hash: bytes,
        plaintext_size: int,
        chunk_id: str,
        batch,
    ) -> None:
        key = self._validate_lookup(object_id, content_hash, plaintext_size)
        validate_uuid(chunk_id, "chunk_id")

        if self.db.get(ColumnFamily.CHUNK_DEDUP_INDEX, key) is not None:
            raise RecordAlreadyExistsError(
                "ChunkDedupIndex mapping already exists for this Object/content"
            )

        self._put_batch(
            batch,
            ColumnFamily.CHUNK_DEDUP_INDEX,
            key,
            {"chunk_id": chunk_id},
        )
