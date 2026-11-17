"""Reconstruct generation payload bytes from any k distinct shards."""

from __future__ import annotations

import hashlib

from engram.processing.artifacts import EncodedShard
from engram.processing.contracts import ErasureCodingConfig
from engram.processing.exceptions import (
    ErasureCodingError,
    InsufficientShardsError,
    ShardIntegrityError,
)

from ._gf256 import _GF_MUL_TABLE, _recovery_matrix
from ._validation import validate_config, validate_original_size


class ReedSolomonDecoder:
    """Decode shards produced by :class:`ReedSolomonEncoder`."""

    def decode(
        self,
        shards: tuple[EncodedShard, ...],
        *,
        original_size: int,
        config: ErasureCodingConfig,
    ) -> bytes:
        validate_config(config)
        validate_original_size(original_size)

        if not isinstance(shards, tuple):
            raise ErasureCodingError("shards must be a tuple")
        if any(not isinstance(shard, EncodedShard) for shard in shards):
            raise ErasureCodingError("shards must contain only EncodedShard artifacts")

        k = config.data_shards
        n = config.total_shards
        if len(shards) < k:
            raise InsufficientShardsError(
                f"at least {k} distinct shards are required; got {len(shards)}"
            )
        if len(shards) > n:
            raise ErasureCodingError(
                f"received {len(shards)} shards but configuration has {n}"
            )

        indices = tuple(shard.index for shard in shards)
        if len(indices) != len(set(indices)):
            raise ErasureCodingError("shard indexes must be unique")
        if any(index < 0 or index >= n for index in indices):
            raise ErasureCodingError("shard index is outside configured range")

        for shard in shards:
            if hashlib.sha256(shard.data).hexdigest() != shard.sha256:
                raise ShardIntegrityError(
                    f"checksum mismatch for shard index {shard.index}"
                )

        shard_size = len(shards[0].data)
        if shard_size <= 0 or any(len(shard.data) != shard_size for shard in shards):
            raise ErasureCodingError(
                "all supplied shards must have the same non-zero size"
            )
        if original_size > shard_size * k:
            raise ErasureCodingError(
                "original_size exceeds the available data-shard capacity"
            )

        selected = shards[:k]
        selected_indices = tuple(shard.index for shard in selected)
        try:
            recovery = _recovery_matrix(k, n, selected_indices)
            selected_bytes = tuple(shard.data for shard in selected)
            recovered_data: list[bytes] = []

            for output_index in range(k):
                output = bytearray(shard_size)
                coefficients = recovery[output_index]
                for input_index, coefficient in enumerate(coefficients):
                    if coefficient == 0:
                        continue
                    source = selected_bytes[input_index]
                    translated = (
                        source
                        if coefficient == 1
                        else source.translate(_GF_MUL_TABLE[coefficient])
                    )
                    for offset, value in enumerate(translated):
                        output[offset] ^= value
                recovered_data.append(bytes(output))

            return b"".join(recovered_data)[:original_size]
        except ErasureCodingError:
            raise
        except Exception as exc:
            raise ErasureCodingError(
                "Failed to reconstruct generation payload"
            ) from exc
