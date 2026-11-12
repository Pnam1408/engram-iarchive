import struct

from .errors import KeyCodecError


class KeyCodec:
    UINT32_MAX = (1 << 32) - 1
    UINT64_MAX = (1 << 64) - 1

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
        if offset < 0 or offset + 4 > len(data):
            raise KeyCodecError("Invalid length prefix")

        length = struct.unpack(">I", data[offset : offset + 4])[0]
        start = offset + 4
        end = start + length

        if end > len(data):
            raise KeyCodecError("Incomplete byte sequence")

        return data[start:end], end

    @staticmethod
    def encode_uint64(value: int) -> bytes:
        if not isinstance(value, int):
            raise TypeError("value must be int")

        if not 0 <= value <= KeyCodec.UINT64_MAX:
            raise ValueError("value must be an unsigned 64-bit integer")

        return struct.pack(">Q", value)

    @staticmethod
    def decode_uint64(
        data: bytes,
        offset: int = 0,
    ) -> tuple[int, int]:
        if offset < 0 or offset + 8 > len(data):
            raise KeyCodecError("Invalid uint64 field")

        value = struct.unpack(">Q", data[offset : offset + 8])[0]
        return value, offset + 8

    @classmethod
    def object_key(cls, object_id: str) -> bytes:
        return cls.encode_bytes(object_id)

    @classmethod
    def version_key(
        cls,
        object_id: str,
        version: int,
    ) -> bytes:
        return cls.encode_bytes(object_id) + cls.encode_uint64(version)

    @classmethod
    def version_chunks_prefix(
        cls,
        object_id: str,
        version: int,
    ) -> bytes:
        return cls.version_key(object_id, version)

    @classmethod
    def version_chunk_key(
        cls,
        object_id: str,
        version: int,
        chunk_index: int,
    ) -> bytes:
        return cls.version_chunks_prefix(object_id, version) + cls.encode_uint64(
            chunk_index
        )

    @classmethod
    def generation_key(cls, generation_id: str) -> bytes:
        return cls.encode_bytes(generation_id)

    @classmethod
    def generation_chunk_key(
        cls,
        generation_id: str,
        record_index: int,
    ) -> bytes:
        return cls.encode_bytes(generation_id) + cls.encode_uint64(record_index)

    @classmethod
    def shard_key(
        cls,
        generation_id: str,
        shard_index: int,
    ) -> bytes:
        return cls.encode_bytes(generation_id) + cls.encode_uint64(shard_index)
