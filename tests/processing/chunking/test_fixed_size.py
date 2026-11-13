from __future__ import annotations

import hashlib

import pytest

from engram.processing.artifacts import Chunk
from engram.processing.chunking import Chunker, FixedSizeChunker
from engram.processing.exceptions import ChunkingError, InvalidConfigurationError


def test_fixed_size_chunker_satisfies_processing_contract() -> None:
    assert isinstance(FixedSizeChunker(4), Chunker)


def test_fixed_size_chunker_partitions_data_and_builds_artifacts() -> None:
    data = b"abcdefghij"

    chunks = FixedSizeChunker(4).chunk(data)

    assert [chunk.data for chunk in chunks] == [b"abcd", b"efgh", b"ij"]
    assert [(chunk.index, chunk.offset, chunk.size) for chunk in chunks] == [
        (0, 0, 4),
        (1, 4, 4),
        (2, 8, 2),
    ]
    assert all(isinstance(chunk, Chunk) for chunk in chunks)
    assert all(chunk.sha256 == hashlib.sha256(chunk.data).hexdigest() for chunk in chunks)
    assert b"".join(chunk.data for chunk in chunks) == data


def test_fixed_size_chunker_empty_input_returns_empty_tuple() -> None:
    assert FixedSizeChunker(4).chunk(b"") == ()


@pytest.mark.parametrize("chunk_size", [0, -1, True, 1.5, "4"])
def test_fixed_size_chunker_rejects_invalid_chunk_size(chunk_size: object) -> None:
    with pytest.raises(InvalidConfigurationError):
        FixedSizeChunker(chunk_size)  # type: ignore[arg-type]


def test_fixed_size_chunker_rejects_non_bytes_input() -> None:
    with pytest.raises(TypeError):
        FixedSizeChunker(4).chunk(bytearray(b"abcd"))  # type: ignore[arg-type]


def test_boundary_builder_rejects_gaps_and_overlaps() -> None:
    from engram.processing.chunking._chunks import build_chunks

    with pytest.raises(ChunkingError):
        build_chunks(b"abcdef", [(0, 2), (3, 3)])
    with pytest.raises(ChunkingError):
        build_chunks(b"abcdef", [(0, 4), (3, 3)])
