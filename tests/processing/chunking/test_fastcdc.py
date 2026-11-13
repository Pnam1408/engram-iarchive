from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from engram.processing.chunking import Chunker, FastCDCChunker, FastCDCConfig
from engram.processing.exceptions import ChunkingError, InvalidConfigurationError


def test_fastcdc_config_defaults_match_existing_research_parameters() -> None:
    config = FastCDCConfig()
    assert (config.min_size, config.avg_size, config.max_size) == (2048, 4096, 8192)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"min_size": 63},
        {"min_size": 2048, "avg_size": 3000, "max_size": 8192},
        {"min_size": 4096, "avg_size": 2048, "max_size": 8192},
        {"min_size": 64, "avg_size": 64, "max_size": 0x1_0000_0000},
        {"min_size": True, "avg_size": 128, "max_size": 256},
    ],
)
def test_fastcdc_config_rejects_invalid_parameters(kwargs: dict[str, int]) -> None:
    with pytest.raises(InvalidConfigurationError):
        FastCDCConfig(**kwargs)


def test_fastcdc_uses_native_boundaries_and_returns_shared_artifacts(monkeypatch) -> None:
    data = b"abcdefgh"
    native = SimpleNamespace(chunk_boundaries=lambda *_args: [(0, 3), (3, 5)])
    monkeypatch.setattr(
        "engram.processing.chunking.fastcdc.import_module",
        lambda _name: native,
    )

    chunker = FastCDCChunker(FastCDCConfig(64, 64, 128))
    chunks = chunker.chunk(data)

    assert isinstance(chunker, Chunker)
    assert [chunk.data for chunk in chunks] == [b"abc", b"defgh"]
    assert [chunk.sha256 for chunk in chunks] == [
        hashlib.sha256(b"abc").hexdigest(),
        hashlib.sha256(b"defgh").hexdigest(),
    ]


def test_fastcdc_empty_input_does_not_load_native_extension(monkeypatch) -> None:
    def fail(_name: str):
        raise AssertionError("native extension should not be loaded")

    monkeypatch.setattr("engram.processing.chunking.fastcdc.import_module", fail)
    assert FastCDCChunker().chunk(b"") == ()


def test_fastcdc_reports_missing_native_extension(monkeypatch) -> None:
    def fail(_name: str):
        raise ImportError("extension missing")

    monkeypatch.setattr("engram.processing.chunking.fastcdc.import_module", fail)
    with pytest.raises(ChunkingError, match="native extension is unavailable"):
        FastCDCChunker().chunk(b"x" * 100)


def test_fastcdc_rejects_invalid_native_boundaries(monkeypatch) -> None:
    native = SimpleNamespace(chunk_boundaries=lambda *_args: [(0, 2), (3, 2)])
    monkeypatch.setattr(
        "engram.processing.chunking.fastcdc.import_module",
        lambda _name: native,
    )
    with pytest.raises(ChunkingError):
        FastCDCChunker().chunk(b"abcde")
