"""Repository for mutable per-Object Version History MMR append state."""

from engram.commitment.mmr import MMRState, Peak, validate_mmr_state

from ..models import MMRStateRecord
from ...database import ColumnFamily, KeyCodec
from ...database.serialization import decode_bytes, deserialize, encode_bytes
from ...repositories import BaseRepository, CorruptMetadataError


class MMRStateRepository(BaseRepository):
    _FIELDS = {"object_id", "protocol", "leaf_count", "peaks"}

    @staticmethod
    def _to_dict(record: MMRStateRecord) -> dict:
        validate_mmr_state(record.state)
        return {
            "object_id": record.object_id,
            "protocol": record.protocol,
            "leaf_count": record.state.leaf_count,
            "peaks": [
                {"height": peak.height, "hash": encode_bytes(peak.hash)}
                for peak in record.state.peaks
            ],
        }

    @classmethod
    def _from_dict(cls, data: dict) -> MMRStateRecord:
        cls._require_fields(data, cls._FIELDS, "MMRStateRecord")
        peaks_data = data["peaks"]
        if not isinstance(peaks_data, list):
            raise ValueError("peaks must be a list")
        peaks = []
        for item in peaks_data:
            if not isinstance(item, dict) or set(item) != {"height", "hash"}:
                raise ValueError("Invalid MMR peak fields")
            peaks.append(
                Peak(
                    height=item["height"], hash=decode_bytes(item["hash"], "peak hash")
                )
            )
        state = MMRState(leaf_count=data["leaf_count"], peaks=tuple(peaks))
        return MMRStateRecord(
            object_id=data["object_id"], protocol=data["protocol"], state=state
        )

    def get(self, object_id: str) -> MMRStateRecord | None:
        self._require_non_empty_string(object_id, "object_id")
        key = KeyCodec.mmr_key(object_id)
        raw = self.db.get(ColumnFamily.MMR, key)
        if raw is None:
            return None
        try:
            data = self._require_dict(deserialize(raw), "MMRStateRecord")
            record = self._from_dict(data)
            if record.state.leaf_count == 0:
                raise CorruptMetadataError("Persisted MMR state must not be empty")
            if record.object_id != object_id:
                raise CorruptMetadataError(
                    "MMRState object_id does not match storage key"
                )
            return record
        except CorruptMetadataError:
            raise
        except Exception as exc:
            raise CorruptMetadataError(
                f"Cannot decode MMRStateRecord for {object_id!r}"
            ) from exc

    def exists(self, object_id: str) -> bool:
        self._require_non_empty_string(object_id, "object_id")
        return self.db.get(ColumnFamily.MMR, KeyCodec.mmr_key(object_id)) is not None

    def put(self, record: MMRStateRecord, batch) -> None:
        if not isinstance(record, MMRStateRecord):
            raise TypeError("record must be an MMRStateRecord")
        if batch is None:
            raise TypeError("batch is required")
        if record.state.leaf_count == 0:
            raise ValueError("Empty MMR state must not be persisted")
        key = KeyCodec.mmr_key(record.object_id)
        self._put_batch(batch, ColumnFamily.MMR, key, self._to_dict(record))
