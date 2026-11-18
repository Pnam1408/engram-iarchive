"""Tests for filesystem storage configuration."""

from pathlib import Path

import pytest

from engram.processing.exceptions import InvalidConfigurationError
from engram.processing.storage.config import FilesystemStorageConfig


class TestFilesystemStorageConfig:
    def test_accept_path_object(self, tmp_path):
        config = FilesystemStorageConfig(root_dir=tmp_path)

        assert isinstance(config.root_dir, Path)
        assert config.root_dir == tmp_path.resolve()

    def test_accept_string_path(self, tmp_path):
        root = str(tmp_path / "storage")

        config = FilesystemStorageConfig(root_dir=root)

        assert isinstance(config.root_dir, Path)
        assert config.root_dir == Path(root).resolve()

    def test_expand_user_directory(self):
        config = FilesystemStorageConfig(root_dir="~/engram-storage")

        assert config.root_dir == Path("~/engram-storage").expanduser().resolve()

    def test_normalize_relative_path(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)

        config = FilesystemStorageConfig(root_dir="data/storage")

        assert config.root_dir == (tmp_path / "data/storage").resolve()

    @pytest.mark.parametrize(
        "root_dir",
        [
            "",
            "   ",
            None,
            123,
            1.5,
            True,
        ],
    )
    def test_reject_invalid_root_dir(self, root_dir):
        with pytest.raises(InvalidConfigurationError):
            FilesystemStorageConfig(root_dir=root_dir)

    def test_config_does_not_create_directory(self, tmp_path):
        root = tmp_path / "not-created"

        config = FilesystemStorageConfig(root_dir=root)

        assert config.root_dir == root.resolve()
        assert not root.exists()

    def test_config_is_immutable(self, tmp_path):
        config = FilesystemStorageConfig(root_dir=tmp_path)

        with pytest.raises(AttributeError):
            config.root_dir = tmp_path / "another"
