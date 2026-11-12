import pytest

from engram.metadata import MetadataDB, MetadataRepositories


@pytest.fixture
def metadata_env(tmp_path):
    with MetadataDB(tmp_path / "metadata") as db:
        yield db, MetadataRepositories(db)
