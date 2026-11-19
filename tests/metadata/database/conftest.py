import pytest

from engram.metadata.database import MetadataDB


@pytest.fixture
def metadata_env(tmp_path):
    with MetadataDB(tmp_path / "metadata") as db:
        yield db
