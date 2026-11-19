from rocksdict import WriteBatch

from .column_families import ColumnFamily


class MetadataBatch:
    """Wrapper for atomic writes across metadata column families."""

    def __init__(self, database):
        self._database = database
        self._batch = WriteBatch(raw_mode=True)
        self._committed = False

    def put(
        self,
        column_family: ColumnFamily | str,
        key: bytes,
        value: bytes,
    ) -> None:
        self._ensure_open()

        cf_handle = self._database.get_column_family_handle(column_family)
        self._batch.put(key, value, cf_handle)

    def delete(
        self,
        column_family: ColumnFamily | str,
        key: bytes,
    ) -> None:
        self._ensure_open()

        cf_handle = self._database.get_column_family_handle(column_family)
        self._batch.delete(key, cf_handle)

    def commit(self) -> None:
        self._ensure_open()
        self._database._write_batch(self._batch)
        self._committed = True

    def _ensure_open(self) -> None:
        if self._committed:
            raise RuntimeError("Batch has already been committed")

    def __enter__(self):
        self._ensure_open()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        # Không tự commit khi thoát context.
        # Chỉ commit khi người gọi chủ động gọi commit().
        return False
