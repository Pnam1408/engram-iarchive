from pathlib import Path
from typing import Iterator

from rocksdict import Options, Rdict

from .batch import MetadataBatch
from .column_families import COLUMN_FAMILIES, ColumnFamily
from .errors import (
    MetadataClosedError,
    UnknownColumnFamilyError,
)


class MetadataDB:
    """Central access layer for ENGRAM metadata stored in RocksDB."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

        self._closed = False
        self._db = self._open_database()

        self._cf_handles = {
            name: self._db.get_column_family_handle(name) for name in COLUMN_FAMILIES
        }

        self._cf_databases = {
            name: self._db.get_column_family(name) for name in COLUMN_FAMILIES
        }

    def _open_database(self) -> Rdict:
        options = Options(raw_mode=True)
        options.create_if_missing(True)
        options.create_missing_column_families(True)

        cf_options = {name: Options(raw_mode=True) for name in COLUMN_FAMILIES}

        return Rdict(
            str(self.path),
            options,
            column_families=cf_options,
        )

    @staticmethod
    def _normalize_cf(
        column_family: ColumnFamily | str,
    ) -> str:
        if isinstance(column_family, ColumnFamily):
            return column_family.value

        if isinstance(column_family, str):
            if column_family in COLUMN_FAMILIES:
                return column_family

        raise UnknownColumnFamilyError(f"Unknown column family: {column_family!r}")

    def _ensure_open(self) -> None:
        if self._closed:
            raise MetadataClosedError("MetadataDB is closed")

    def get_column_family_handle(self, column_family):
        self._ensure_open()
        name = self._normalize_cf(column_family)
        return self._cf_handles[name]

    def get(
        self,
        column_family: ColumnFamily | str,
        key: bytes,
    ) -> bytes | None:
        self._ensure_open()
        name = self._normalize_cf(column_family)
        return self._cf_databases[name].get(key)

    def put(
        self,
        column_family: ColumnFamily | str,
        key: bytes,
        value: bytes,
    ) -> None:
        self._ensure_open()
        name = self._normalize_cf(column_family)
        self._cf_databases[name].put(key, value)

    def delete(
        self,
        column_family: ColumnFamily | str,
        key: bytes,
    ) -> None:
        self._ensure_open()
        name = self._normalize_cf(column_family)
        self._cf_databases[name].delete(key)

    def iter_prefix(
        self,
        column_family: ColumnFamily | str,
        prefix: bytes,
    ) -> Iterator[tuple[bytes, bytes]]:
        """Iterate over entries whose keys start with prefix."""
        self._ensure_open()
        name = self._normalize_cf(column_family)

        iterator = self._cf_databases[name].iter()
        iterator.seek(prefix)

        while iterator.valid():
            key = iterator.key()

            if not key.startswith(prefix):
                break

            yield key, iterator.value()
            iterator.next()

    def batch(self) -> MetadataBatch:
        self._ensure_open()
        return MetadataBatch(self)

    def _write_batch(self, batch) -> None:
        self._ensure_open()
        self._db.write(batch)

    def close(self) -> None:
        if self._closed:
            return

        self._db.close()
        self._closed = True
        self._cf_handles.clear()
        self._cf_databases.clear()

    def __enter__(self):
        self._ensure_open()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False
