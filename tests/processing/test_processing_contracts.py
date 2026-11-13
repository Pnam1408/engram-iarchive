from __future__ import annotations

from engram.chunking import FastCDCChunker, FixedSizeChunker
from engram.processing.contracts import Chunker, ErasureCodingConfig
from engram.processing.exceptions import InvalidConfigurationError


def test_existing_chunkers_satisfy_shared_chunker_contract() -> None:
    fixed = FixedSizeChunker(4)
    fastcdc = FastCDCChunker()
    assert isinstance(fixed, Chunker)
    assert isinstance(fastcdc, Chunker)

    chunks = fixed.chunk(b"contract smoke test")
    assert b"".join(chunk.data for chunk in chunks) == b"contract smoke test"


def test_erasure_coding_config_defaults_and_validation() -> None:
    assert ErasureCodingConfig() == ErasureCodingConfig(4, 6)

    try:
        ErasureCodingConfig(4, 4)
    except InvalidConfigurationError:
        pass
    else:
        raise AssertionError("invalid erasure coding configuration was accepted")
