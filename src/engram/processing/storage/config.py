"""Storage backend configuration."""

from dataclasses import dataclass
from pathlib import Path

from engram.processing.exceptions import InvalidConfigurationError


@dataclass(frozen=True, slots=True)
class FilesystemStorageConfig:
    """Configuration for the local filesystem storage backend."""

    root_dir: Path

    def __post_init__(self) -> None:
        if isinstance(self.root_dir, str):
            if not self.root_dir.strip():
                raise InvalidConfigurationError("root_dir must not be empty")
            root = Path(self.root_dir)
        elif isinstance(self.root_dir, Path):
            root = self.root_dir
        else:
            raise InvalidConfigurationError("root_dir must be a str or pathlib.Path")

        try:
            root = root.expanduser().resolve()
        except (OSError, RuntimeError) as exc:
            raise InvalidConfigurationError("root_dir cannot be resolved") from exc

        object.__setattr__(self, "root_dir", root)
