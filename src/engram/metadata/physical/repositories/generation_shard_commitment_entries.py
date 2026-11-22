from ..models import GenerationShardCommitmentEntry
from ...database import ColumnFamily, KeyCodec
from ...database.serialization import decode_bytes, deserialize, encode_bytes
from ...repositories import (
    BaseRepository,
    CorruptMetadataError,
    RecordAlreadyExistsError,
)


class GenerationShardCommitmentEntryRepository(BaseRepository):
    _FIELDS = {"generation_id", "shard_index", "shard_root"}

    @staticmethod
    def _to_dict(entry: GenerationShardCommitmentEntry) -> dict:
        return {
            "generation_id": entry.generation_id,
            "shard_index": entry.shard_index,
            "shard_root": encode_bytes(entry.shard_root),
        }

    @classmethod
    def _from_dict(cls, data: dict) -> GenerationShardCommitmentEntry:
        cls._require_fields(data, cls._FIELDS, "GenerationShardCommitmentEntry")
        return GenerationShardCommitmentEntry(
            generation_id=data["generation_id"],
            shard_index=data["shard_index"],
            shard_root=decode_bytes(data["shard_root"], "shard_root"),
        )

    @staticmethod
    def _index_from_key(key: bytes, prefix: bytes) -> int:
        if not key.startswith(prefix) or len(key) != len(prefix) + 8:
            raise CorruptMetadataError("Invalid shard commitment entry storage key")
        try:
            index, end = KeyCodec.decode_uint64(key, len(prefix))
            if end != len(key):
                raise CorruptMetadataError("Invalid shard commitment entry key length")
            return index
        except Exception as exc:
            raise CorruptMetadataError(
                "Cannot decode shard commitment entry key"
            ) from exc

    def _decode(
        self, raw: bytes, generation_id: str, shard_index: int
    ) -> GenerationShardCommitmentEntry:
        try:
            data = self._require_dict(
                deserialize(raw), "GenerationShardCommitmentEntry"
            )
            entry = self._from_dict(data)
            if entry.generation_id != generation_id or entry.shard_index != shard_index:
                raise CorruptMetadataError(
                    "Shard commitment entry fields mismatch storage key"
                )
            return entry
        except CorruptMetadataError:
            raise
        except Exception as exc:
            raise CorruptMetadataError("Cannot decode shard commitment entry") from exc

    def get(
        self, generation_id: str, shard_index: int
    ) -> GenerationShardCommitmentEntry | None:
        self._require_non_empty_string(generation_id, "generation_id")
        self._require_non_negative_int(shard_index, "shard_index")
        key = KeyCodec.generation_shard_commitment_entry_key(generation_id, shard_index)
        raw = self.db.get(ColumnFamily.GENERATION_SHARD_COMMITMENT_ENTRIES, key)
        return None if raw is None else self._decode(raw, generation_id, shard_index)

    def list_for_generation(
        self, generation_id: str
    ) -> tuple[GenerationShardCommitmentEntry, ...]:
        self._require_non_empty_string(generation_id, "generation_id")
        prefix = KeyCodec.generation_shard_commitment_entry_prefix(generation_id)
        entries = []
        for key, raw in self.db.iter_prefix(
            ColumnFamily.GENERATION_SHARD_COMMITMENT_ENTRIES, prefix
        ):
            index = self._index_from_key(key, prefix)
            entries.append(self._decode(raw, generation_id, index))
        if any(a.shard_index >= b.shard_index for a, b in zip(entries, entries[1:])):
            raise CorruptMetadataError(
                "Shard commitment entries are not strictly ordered"
            )
        return tuple(entries)

    def put(self, entry: GenerationShardCommitmentEntry, batch) -> None:
        if not isinstance(entry, GenerationShardCommitmentEntry):
            raise TypeError("entry must be GenerationShardCommitmentEntry")
        if batch is None:
            raise TypeError("batch is required")
        key = KeyCodec.generation_shard_commitment_entry_key(
            entry.generation_id, entry.shard_index
        )
        if (
            self.db.get(ColumnFamily.GENERATION_SHARD_COMMITMENT_ENTRIES, key)
            is not None
        ):
            raise RecordAlreadyExistsError("Shard commitment entry already exists")
        self._put_batch(
            batch,
            ColumnFamily.GENERATION_SHARD_COMMITMENT_ENTRIES,
            key,
            self._to_dict(entry),
        )

    def put_all(
        self, entries: tuple[GenerationShardCommitmentEntry, ...], batch
    ) -> None:
        if not isinstance(entries, tuple) or any(
            not isinstance(e, GenerationShardCommitmentEntry) for e in entries
        ):
            raise TypeError("entries must be a tuple of GenerationShardCommitmentEntry")
        if batch is None:
            raise TypeError("batch is required")
        if len({(e.generation_id, e.shard_index) for e in entries}) != len(entries):
            raise ValueError("Duplicate shard commitment entry key")
        # Preflight before staging any entry writes.
        for e in entries:
            key = KeyCodec.generation_shard_commitment_entry_key(
                e.generation_id, e.shard_index
            )
            if (
                self.db.get(ColumnFamily.GENERATION_SHARD_COMMITMENT_ENTRIES, key)
                is not None
            ):
                raise RecordAlreadyExistsError("Shard commitment entry already exists")
        for e in entries:
            self.put(e, batch)
