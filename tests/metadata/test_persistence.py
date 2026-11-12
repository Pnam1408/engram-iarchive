from engram.metadata import MetadataDB, ColumnFamily


def test_data_persists_after_reopen(tmp_path):
    path = tmp_path / "metadata"

    with MetadataDB(path) as db:
        db.put(
            ColumnFamily.OBJECTS,
            b"object-001",
            b"persistent-object",
        )

        db.put(
            ColumnFamily.CHUNKS,
            b"chunk-001",
            b"persistent-chunk",
        )

    with MetadataDB(path) as db:
        assert (
            db.get(
                ColumnFamily.OBJECTS,
                b"object-001",
            )
            == b"persistent-object"
        )

        assert (
            db.get(
                ColumnFamily.CHUNKS,
                b"chunk-001",
            )
            == b"persistent-chunk"
        )
