"""Reed-Solomon encoding for generation payloads."""

from __future__ import annotations

import hashlib
from math import ceil

from reedsolo import RSCodec

from engram.processing.artifacts import (
    EncodedShard,
    EncodedShardSet,
    GenerationPayload,
)
from engram.processing.contracts import ErasureCodingConfig
from engram.processing.exceptions import ErasureCodingError, InvalidArtifactError

from ._validation import validate_config


class ReedSolomonEncoder:
    """Encode one packed generation payload into equally sized shards.
    The layout intentionally matches the legacy implementation: split the
    payload into k equally sized data shards (zero-padded), then RS-encode
    each byte column independently into n shard bytes.
    """

    def encode(
        self,
        payload: GenerationPayload,
        *,
        config: ErasureCodingConfig,
    ) -> EncodedShardSet:
        if not isinstance(payload, GenerationPayload):
            raise InvalidArtifactError("payload must be a GenerationPayload artifact")
        validate_config(config)

        try:
            k = config.data_shards
            n = config.total_shards
            shard_size = max(1, ceil(len(payload.data) / k))
            padded = payload.data.ljust(shard_size * k, b"\x00")
            data_parts = tuple(
                padded[index * shard_size : (index + 1) * shard_size]
                for index in range(k)
            )

            output = [bytearray(shard_size) for _ in range(n)]
            codec = RSCodec(n - k)
            for offset in range(shard_size):
                message = bytes(part[offset] for part in data_parts)
                codeword = bytes(codec.encode(message))
                if len(codeword) != n:
                    raise ErasureCodingError(
                        "Reed-Solomon codec returned an unexpected codeword size"
                    )
                for shard_index, value in enumerate(codeword):
                    output[shard_index][offset] = value

            shards = tuple(
                EncodedShard(
                    index=index,
                    data=bytes(data),
                    sha256=hashlib.sha256(data).hexdigest(),
                )
                for index, data in enumerate(output)
            )
            return EncodedShardSet(
                shards=shards,
                original_size=len(payload.data),
                data_shards=k,
                total_shards=n,
            )
        except ErasureCodingError:
            raise
        except Exception as exc:
            raise ErasureCodingError("Failed to encode generation payload") from exc
