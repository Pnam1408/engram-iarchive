import struct

from fractional_indexing import validate_order_key

from .errors import KeyCodecError


class KeyCodec:
    UINT32_MAX = (1 << 32) - 1
    UINT64_MAX = (1 << 64) - 1
    SHA256_SIZE = 32

    # ------------------------------------------------------------------
    # Primitive encoding
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_offset(data: bytes, offset: int) -> None:
        if not isinstance(data, bytes):
            raise TypeError("data must be bytes")

        if isinstance(offset, bool) or not isinstance(offset, int):
            raise TypeError("offset must be an integer")

        if not 0 <= offset <= len(data):
            raise KeyCodecError("Invalid offset")

    @staticmethod
    def encode_bytes(value: str | bytes) -> bytes:
        if isinstance(value, str):
            raw = value.encode("utf-8")
        elif isinstance(value, bytes):
            raw = value
        else:
            raise TypeError("value must be str or bytes")

        if len(raw) > KeyCodec.UINT32_MAX:
            raise ValueError("value is too long")

        return struct.pack(">I", len(raw)) + raw

    @staticmethod
    def decode_bytes(
        data: bytes,
        offset: int = 0,
    ) -> tuple[bytes, int]:
        KeyCodec._validate_offset(data, offset)

        if offset + 4 > len(data):
            raise KeyCodecError("Incomplete length prefix")

        length = struct.unpack_from(">I", data, offset)[0]

        start = offset + 4
        end = start + length

        if end > len(data):
            raise KeyCodecError("Incomplete byte sequence")

        return data[start:end], end

    @classmethod
    def encode_string(cls, value: str) -> bytes:
        if not isinstance(value, str):
            raise TypeError("value must be a string")

        if not value:
            raise ValueError("string must not be empty")

        return cls.encode_bytes(value)

    @classmethod
    def decode_string(
        cls,
        data: bytes,
        offset: int = 0,
    ) -> tuple[str, int]:
        raw, end = cls.decode_bytes(data, offset)

        try:
            value = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise KeyCodecError("Invalid UTF-8 string") from exc

        if not value:
            raise KeyCodecError("Decoded string must not be empty")

        return value, end

    @staticmethod
    def encode_uint64(value: int) -> bytes:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("value must be an integer (bool is not accepted)")

        if not 0 <= value <= KeyCodec.UINT64_MAX:
            raise ValueError("value must be an unsigned 64-bit integer")

        return struct.pack(">Q", value)

    @staticmethod
    def decode_uint64(
        data: bytes,
        offset: int = 0,
    ) -> tuple[int, int]:
        KeyCodec._validate_offset(data, offset)

        if offset + 8 > len(data):
            raise KeyCodecError("Incomplete uint64 field")

        value = struct.unpack_from(">Q", data, offset)[0]
        return value, offset + 8

    @classmethod
    def encode_hash(cls, value: bytes) -> bytes:
        if not isinstance(value, bytes):
            raise TypeError("hash must be bytes")

        if len(value) != cls.SHA256_SIZE:
            raise ValueError("SHA-256 hash must contain exactly 32 bytes")

        return value

    @classmethod
    def decode_hash(
        cls,
        data: bytes,
        offset: int = 0,
    ) -> tuple[bytes, int]:
        cls._validate_offset(data, offset)

        end = offset + cls.SHA256_SIZE

        if end > len(data):
            raise KeyCodecError("Incomplete SHA-256 hash")

        return data[offset:end], end

    # ------------------------------------------------------------------
    # OrderKey
    # ------------------------------------------------------------------

    @staticmethod
    def encode_order_key(value: str) -> bytes:
        if not isinstance(value, str):
            raise TypeError("OrderKey must be a string")

        try:
            validate_order_key(value)
            return value.encode("ascii")
        except (ValueError, UnicodeEncodeError) as exc:
            raise ValueError("Invalid OrderKey") from exc

    @staticmethod
    def decode_order_key(data: bytes) -> str:
        if not isinstance(data, bytes):
            raise TypeError("OrderKey data must be bytes")

        try:
            value = data.decode("ascii")
            validate_order_key(value)
            return value
        except (ValueError, UnicodeDecodeError) as exc:
            raise KeyCodecError("Invalid OrderKey") from exc

    # ------------------------------------------------------------------
    # Object
    # ------------------------------------------------------------------

    @classmethod
    def object_key(cls, object_id: str) -> bytes:
        return cls.encode_string(object_id)

    # ------------------------------------------------------------------
    # Version
    # ------------------------------------------------------------------

    @classmethod
    def version_prefix(cls, object_id: str) -> bytes:
        return cls.encode_string(object_id)

    @classmethod
    def version_key(
        cls,
        object_id: str,
        sequence: int,
    ) -> bytes:
        return cls.version_prefix(object_id) + cls.encode_uint64(sequence)

    # ------------------------------------------------------------------
    # Version Delta
    # ------------------------------------------------------------------

    @classmethod
    def version_delta_prefix(cls, object_id: str) -> bytes:
        return cls.encode_string(object_id)

    @classmethod
    def version_delta_key(
        cls,
        object_id: str,
        sequence: int,
    ) -> bytes:
        return cls.version_delta_prefix(object_id) + cls.encode_uint64(sequence)

    # ------------------------------------------------------------------
    # Chunk Catalog
    # ------------------------------------------------------------------

    @classmethod
    def chunk_prefix(cls, object_id: str) -> bytes:
        return cls.encode_string(object_id)

    @classmethod
    def chunk_key(
        cls,
        object_id: str,
        chunk_id: str,
    ) -> bytes:
        return cls.chunk_prefix(object_id) + cls.encode_string(chunk_id)

    # ------------------------------------------------------------------
    # Chunk Dedup Index
    # ------------------------------------------------------------------

    @classmethod
    def chunk_dedup_prefix(cls, object_id: str) -> bytes:
        return cls.encode_string(object_id)

    @classmethod
    def chunk_dedup_key(
        cls,
        object_id: str,
        content_hash: bytes,
        plaintext_size: int,
    ) -> bytes:
        return (
            cls.chunk_dedup_prefix(object_id)
            + cls.encode_hash(content_hash)
            + cls.encode_uint64(plaintext_size)
        )

    # ------------------------------------------------------------------
    # Object Chunk Index
    # ------------------------------------------------------------------

    @classmethod
    def object_chunk_index_prefix(cls, object_id: str) -> bytes:
        return cls.encode_string(object_id)

    @classmethod
    def object_chunk_index_state_key(cls, object_id: str) -> bytes:
        """
        Reserved per-Object index-state key.

        The NUL suffix cannot be a valid fractional OrderKey, so it cannot
        collide with an entry key in the same Object prefix.
        """
        return cls.object_chunk_index_prefix(object_id) + b"\x00"

    @classmethod
    def object_chunk_index_key(
        cls,
        object_id: str,
        order_key: str,
    ) -> bytes:
        return cls.object_chunk_index_prefix(object_id) + cls.encode_order_key(
            order_key
        )

    # ------------------------------------------------------------------
    # Checkpoint
    # ------------------------------------------------------------------

    @classmethod
    def checkpoint_prefix(cls, object_id: str) -> bytes:
        return cls.encode_string(object_id)

    @classmethod
    def checkpoint_key(
        cls,
        object_id: str,
        sequence: int,
    ) -> bytes:
        return cls.checkpoint_prefix(object_id) + cls.encode_uint64(sequence)

    # ------------------------------------------------------------------
    # Checkpoint Entries
    # ------------------------------------------------------------------

    @classmethod
    def checkpoint_entry_prefix(
        cls,
        object_id: str,
        sequence: int,
    ) -> bytes:
        return cls.encode_string(object_id) + cls.encode_uint64(sequence)

    @classmethod
    def checkpoint_entry_key(
        cls,
        object_id: str,
        sequence: int,
        order_key: str,
    ) -> bytes:
        return cls.checkpoint_entry_prefix(object_id, sequence) + cls.encode_order_key(
            order_key
        )

    # ------------------------------------------------------------------
    # MMR
    # ------------------------------------------------------------------

    @classmethod
    def mmr_key(cls, object_id: str) -> bytes:
        return cls.encode_string(object_id)

    # ------------------------------------------------------------------
    # Physical Metadata - Generation Manifest
    # ------------------------------------------------------------------

    @classmethod
    def generation_manifest_key(cls, generation_id: str) -> bytes:
        """Return the key for the single manifest belonging to a Generation."""
        return cls.encode_string(generation_id)

    # ------------------------------------------------------------------
    # Physical Metadata - Generation Manifest Entries
    # ------------------------------------------------------------------

    @classmethod
    def generation_manifest_entry_prefix(cls, generation_id: str) -> bytes:
        return cls.encode_string(generation_id)

    @classmethod
    def generation_manifest_entry_key(
        cls,
        generation_id: str,
        offset: int,
    ) -> bytes:
        """Key entries by payload offset so prefix iteration is ordered."""
        return (
            cls.generation_manifest_entry_prefix(generation_id)
            + cls.encode_uint64(offset)
        )

    # ------------------------------------------------------------------
    # Physical Metadata - Generation Shards
    # ------------------------------------------------------------------

    @classmethod
    def generation_shard_prefix(cls, generation_id: str) -> bytes:
        return cls.encode_string(generation_id)

    @classmethod
    def generation_shard_key(
        cls,
        generation_id: str,
        shard_index: int,
    ) -> bytes:
        return (
            cls.generation_shard_prefix(generation_id)
            + cls.encode_uint64(shard_index)
        )

    # ------------------------------------------------------------------
    # Physical Metadata - Chunk Placements
    # ------------------------------------------------------------------

    @classmethod
    def chunk_placement_key(cls, placement_id: str) -> bytes:
        return cls.encode_string(placement_id)

    @classmethod
    def chunk_placement_index_prefix(
        cls,
        object_id: str,
        chunk_id: str,
    ) -> bytes:
        return cls.encode_string(object_id) + cls.encode_string(chunk_id)

    @classmethod
    def chunk_placement_index_key(
        cls,
        object_id: str,
        chunk_id: str,
        placement_id: str,
    ) -> bytes:
        return (
            cls.chunk_placement_index_prefix(object_id, chunk_id)
            + cls.encode_string(placement_id)
        )

    # ------------------------------------------------------------------
    # Physical Metadata - Shard Placements
    # ------------------------------------------------------------------

    @classmethod
    def shard_placement_key(cls, placement_id: str) -> bytes:
        return cls.encode_string(placement_id)

    @classmethod
    def shard_placement_index_prefix(
        cls,
        generation_id: str,
        shard_index: int,
    ) -> bytes:
        return (
            cls.encode_string(generation_id)
            + cls.encode_uint64(shard_index)
        )

    @classmethod
    def shard_placement_index_key(
        cls,
        generation_id: str,
        shard_index: int,
        placement_id: str,
    ) -> bytes:
        return (
            cls.shard_placement_index_prefix(generation_id, shard_index)
            + cls.encode_string(placement_id)
        )

    # ------------------------------------------------------------------
    # Physical Metadata - Storage Receipts
    # ------------------------------------------------------------------

    @classmethod
    def storage_receipt_key(cls, receipt_id: str) -> bytes:
        return cls.encode_string(receipt_id)

    @classmethod
    def storage_receipt_index_prefix(cls, placement_id: str) -> bytes:
        return cls.encode_string(placement_id)

    @classmethod
    def storage_receipt_index_key(
        cls,
        placement_id: str,
        issued_at: int,
        receipt_id: str,
    ) -> bytes:
        """Key receipts by placement and issue time, with ID as tie-breaker."""
        return (
            cls.storage_receipt_index_prefix(placement_id)
            + cls.encode_uint64(issued_at)
            + cls.encode_string(receipt_id)
        )

    # ------------------------------------------------------------------
    # Legacy physical metadata keys
    # Keep temporarily until Physical Metadata is redesigned.
    # ------------------------------------------------------------------

    @classmethod
    def generation_key(cls, generation_id: str) -> bytes:
        return cls.encode_string(generation_id)

    @classmethod
    def generation_chunk_key(
        cls,
        generation_id: str,
        record_index: int,
    ) -> bytes:
        return cls.encode_string(generation_id) + cls.encode_uint64(record_index)

    @classmethod
    def shard_key(
        cls,
        generation_id: str,
        shard_index: int,
    ) -> bytes:
        return cls.encode_string(generation_id) + cls.encode_uint64(shard_index)
