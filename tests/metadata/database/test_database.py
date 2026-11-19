import pytest

from engram.metadata.database import MetadataDB, ColumnFamily
from engram.metadata.database.errors import (
    MetadataClosedError,
    UnknownColumnFamilyError,
)


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
