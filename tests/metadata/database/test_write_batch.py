from engram.metadata.database import MetadataDB, ColumnFamily


def test_cross_column_family_write_batch(tmp_path):
    with MetadataDB(tmp_path / "metadata") as db:
        with db.batch() as batch:
            batch.put(
                ColumnFamily.OBJECTS,
                b"object-001",
                b"object-data",
            )

            batch.put(
                ColumnFamily.CHUNKS,
                b"chunk-001",
                b"chunk-data",
            )

            batch.put(
                ColumnFamily.GENERATIONS,
                b"generation-001",
                b"generation-data",
            )

            batch.commit()

        assert (
            db.get(
                ColumnFamily.OBJECTS,
                b"object-001",
            )
            == b"object-data"
        )

        assert (
            db.get(
                ColumnFamily.CHUNKS,
                b"chunk-001",
            )
            == b"chunk-data"
        )

        assert (
            db.get(
                ColumnFamily.GENERATIONS,
                b"generation-001",
            )
            == b"generation-data"
        )


def test_batch_delete(tmp_path):
    with MetadataDB(tmp_path / "metadata") as db:
        db.put(ColumnFamily.OBJECTS, b"object-001", b"value")

        with db.batch() as batch:
            batch.delete(ColumnFamily.OBJECTS, b"object-001")
            batch.commit()

        assert db.get(ColumnFamily.OBJECTS, b"object-001") is None
