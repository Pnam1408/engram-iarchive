from __future__ import annotations

from engram.processing.contracts import Chunker, ErasureCodingConfig
from engram.processing.exceptions import InvalidConfigurationError


def test_erasure_coding_config_defaults_and_validation() -> None:
    assert ErasureCodingConfig() == ErasureCodingConfig(4, 6)

    try:
        ErasureCodingConfig(4, 4)
    except InvalidConfigurationError:
        pass
    else:
        raise AssertionError("invalid erasure coding configuration was accepted")
