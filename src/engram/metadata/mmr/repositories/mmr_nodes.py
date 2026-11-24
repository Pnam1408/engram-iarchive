"""Repository for immutable Version History MMR internal nodes."""

from ..models import MMRNodeRecord
from ...database import ColumnFamily, KeyCodec
from ...database.serialization import decode_bytes, deserialize, encode_bytes
from ...repositories import BaseRepository, CorruptMetadataError


class MMRNodeRepository(BaseRepository):
    _FIELDS = {"object_id", "start_leaf", "height", "hash"}

    @staticmethod
    def _to_dict(record: MMRNodeRecord) -> dict:
        return {
            "object_id": record.object_id,
            "start_leaf": record.start_leaf,
            "height": record.height,
            "hash": encode_bytes(record.hash),
        }

    @classmethod
    def _from_dict(cls, data: dict) -> MMRNodeRecord:
        cls._require_fields(data, cls._FIELDS, "MMRNodeRecord")
        return MMRNodeRecord(
            object_id=data["object_id"],
            start_leaf=data["start_leaf"],
            height=data["height"],
            hash=decode_bytes(data["hash"], "hash"),
        )

    @staticmethod
    def _position_from_key(key: bytes, prefix: bytes) -> tuple[int, int]:
        if not key.startswith(prefix) or len(key) != len(prefix) + 9:
            raise CorruptMetadataError("Invalid MMR node storage key")
        try:
            start_leaf, offset = KeyCodec.decode_uint64(key, len(prefix))
            height, end = KeyCodec.decode_uint8(key, offset)
            if end != len(key):
                raise CorruptMetadataError("Invalid MMR node key length")
            if not 1 <= height <= 63 or start_leaf % (1 << height):
                raise CorruptMetadataError("Invalid MMR node key coordinates")
            return start_leaf, height
        except CorruptMetadataError:
            raise
        except Exception as exc:
            raise CorruptMetadataError("Cannot decode MMR node storage key") from exc

    def _decode(
        self, raw: bytes, object_id: str, start_leaf: int, height: int
    ) -> MMRNodeRecord:
        try:
            data = self._require_dict(deserialize(raw), "MMRNodeRecord")
            record = self._from_dict(data)
            if (record.object_id, record.start_leaf, record.height) != (
                object_id,
                start_leaf,
                height,
            ):
                raise CorruptMetadataError("MMR node fields do not match storage key")
            return record
        except CorruptMetadataError:
            raise
        except Exception as exc:
            raise CorruptMetadataError("Cannot decode MMRNodeRecord") from exc

    def get(self, object_id: str, start_leaf: int, height: int) -> MMRNodeRecord | None:
        self._require_non_empty_string(object_id, "object_id")
        key = KeyCodec.mmr_node_key(object_id, start_leaf, height)
        raw = self.db.get(ColumnFamily.MMR_NODES, key)
        return None if raw is None else self._decode(raw, object_id, start_leaf, height)

    def exists(self, object_id: str, start_leaf: int, height: int) -> bool:
        self._require_non_empty_string(object_id, "object_id")
        key = KeyCodec.mmr_node_key(object_id, start_leaf, height)
        return self.db.get(ColumnFamily.MMR_NODES, key) is not None

    def list_for_object(self, object_id: str) -> tuple[MMRNodeRecord, ...]:
        self._require_non_empty_string(object_id, "object_id")
        prefix = KeyCodec.mmr_node_prefix(object_id)
        records = []
        for key, raw in self.db.iter_prefix(ColumnFamily.MMR_NODES, prefix):
            start_leaf, height = self._position_from_key(key, prefix)
            records.append(self._decode(raw, object_id, start_leaf, height))
        # RocksDB byte order is the key's lexicographic order (start_leaf, height).
        if any(
            (a.start_leaf, a.height) >= (b.start_leaf, b.height)
            for a, b in zip(records, records[1:])
        ):
            raise CorruptMetadataError("MMR nodes are not strictly ordered")
        return tuple(records)

    def put(self, record: MMRNodeRecord, batch) -> None:
        if not isinstance(record, MMRNodeRecord):
            raise TypeError("record must be an MMRNodeRecord")
        if batch is None:
            raise TypeError("batch is required")
        key = KeyCodec.mmr_node_key(record.object_id, record.start_leaf, record.height)
        existing = self.get(record.object_id, record.start_leaf, record.height)
        if existing is not None:
            if existing.hash == record.hash:
                return
            raise CorruptMetadataError(
                "MMR internal node coordinate already exists with a different hash"
            )
        self._put_batch(batch, ColumnFamily.MMR_NODES, key, self._to_dict(record))

    def put_all(self, records: tuple[MMRNodeRecord, ...], batch) -> None:
        if not isinstance(records, tuple) or any(
            not isinstance(r, MMRNodeRecord) for r in records
        ):
            raise TypeError("records must be a tuple of MMRNodeRecord")
        if batch is None:
            raise TypeError("batch is required")
        keys = [(r.object_id, r.start_leaf, r.height) for r in records]
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate MMR node coordinate in input")
        # Preflight all records before staging any writes.
        for record in records:
            existing = self.get(record.object_id, record.start_leaf, record.height)
            if existing is not None and existing.hash != record.hash:
                raise CorruptMetadataError(
                    "MMR internal node coordinate already exists with a different hash"
                )
        for record in records:
            self.put(record, batch)
