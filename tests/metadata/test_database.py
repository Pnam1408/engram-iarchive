import pytest

from engram.metadata import MetadataDB, ColumnFamily
from engram.metadata.database.errors import (
    MetadataClosedError,
    UnknownColumnFamilyError,
)
from engram.metadata.database.key_codec import KeyCodec


def test_all_column_families_exist(tmp_path):
    with MetadataDB(tmp_path / "metadata") as db:
        for cf in ColumnFamily:
            assert db.get_column_family_handle(cf) is not None


def test_crud(tmp_path):
    with MetadataDB(tmp_path / "metadata") as db:
        key = b"test-key"
        value = b"test-value"

        db.put(ColumnFamily.OBJECTS, key, value)

        assert db.get(ColumnFamily.OBJECTS, key) == value

        db.delete(ColumnFamily.OBJECTS, key)

        assert db.get(ColumnFamily.OBJECTS, key) is None


def test_unknown_column_family(tmp_path):
    with MetadataDB(tmp_path / "metadata") as db:
        with pytest.raises(UnknownColumnFamilyError):
            db.get("unknown_cf", b"key")


def test_access_after_close(tmp_path):
    db = MetadataDB(tmp_path / "metadata")
    db.close()

    with pytest.raises(MetadataClosedError):
        db.get(ColumnFamily.OBJECTS, b"key")


def test_prefix_scan_version_chunks(tmp_path):
    with MetadataDB(tmp_path / "metadata") as db:
        prefix = KeyCodec.version_chunks_prefix(
            "object-001",
            2,
        )

        for index in range(3):
            key = KeyCodec.version_chunk_key(
                "object-001",
                2,
                index,
            )
            db.put(
                ColumnFamily.VERSION_CHUNKS,
                key,
                f"chunk-{index}".encode(),
            )

        # Một version khác không được xuất hiện trong kết quả.
        other_key = KeyCodec.version_chunk_key(
            "object-001",
            3,
            0,
        )
        db.put(
            ColumnFamily.VERSION_CHUNKS,
            other_key,
            b"other-version",
        )

        results = list(
            db.iter_prefix(
                ColumnFamily.VERSION_CHUNKS,
                prefix,
            )
        )

        assert len(results) == 3

        values = [value for _, value in results]
        assert values == [
            b"chunk-0",
            b"chunk-1",
            b"chunk-2",
        ]
