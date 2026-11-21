from __future__ import annotations

import hashlib
from dataclasses import replace
from itertools import combinations

import pytest

from engram.processing.artifacts import GenerationPayload, EncodedShard
from engram.processing.artifacts.payload import PayloadRecord
from engram.processing.contracts import ErasureCodingConfig
from engram.processing.exceptions import (
    ErasureCodingError,
    InsufficientShardsError,
    InvalidArtifactError,
    ShardIntegrityError,
)
from engram.processing.erasure_coding import (
    ReedSolomonDecoder,
    ReedSolomonEncoder,
)


def make_payload(data: bytes) -> GenerationPayload:
    return GenerationPayload(
        data=data,
        records=(
            (
                PayloadRecord(
                    record_index=0,
                    object_id="test-object",
                    chunk_id="test-chunk",
                    offset=0,
                    length=len(data),
                ),
            )
            if data
            else ()
        ),
        format_version=1,
        sha256=hashlib.sha256(data).hexdigest(),
    )


@pytest.fixture
def encoder():
    return ReedSolomonEncoder()


@pytest.fixture
def decoder():
    return ReedSolomonDecoder()


@pytest.mark.parametrize(
    "config",
    [
        ErasureCodingConfig(2, 4),
        ErasureCodingConfig(3, 5),
        ErasureCodingConfig(4, 6),
    ],
)
@pytest.mark.parametrize(
    "data",
    [b"", b"x", b"short payload", bytes(range(256)) * 3],
    ids=["empty", "one-byte", "text", "binary"],
)
def test_encode_decode_all_shards(encoder, decoder, config, data):
    payload = make_payload(data)
    encoded = encoder.encode(payload, config=config)

    assert encoded.original_size == len(data)
    assert encoded.data_shards == config.data_shards
    assert encoded.total_shards == config.total_shards
    assert tuple(shard.index for shard in encoded.shards) == tuple(
        range(config.total_shards)
    )
    assert len({len(shard.data) for shard in encoded.shards}) == 1

    recovered = decoder.decode(
        encoded.shards,
        original_size=len(data),
        config=config,
    )
    assert recovered == data


def test_every_k_shard_combination_recovers_payload(encoder, decoder):
    config = ErasureCodingConfig(data_shards=2, total_shards=4)
    data = bytes(range(251)) + b"ENGRAM"
    encoded = encoder.encode(make_payload(data), config=config)

    for selected in combinations(encoded.shards, config.data_shards):
        assert (
            decoder.decode(
                tuple(selected),
                original_size=len(data),
                config=config,
            )
            == data
        )


def test_decoder_accepts_more_than_k_shards_and_uses_first_k(encoder, decoder):
    config = ErasureCodingConfig(2, 4)
    data = b"more shards than required"
    encoded = encoder.encode(make_payload(data), config=config)
    supplied = (encoded.shards[3], encoded.shards[1], encoded.shards[0])
    assert decoder.decode(supplied, original_size=len(data), config=config) == data


def test_decoder_rejects_insufficient_shards(encoder, decoder):
    config = ErasureCodingConfig(3, 5)
    encoded = encoder.encode(make_payload(b"abc"), config=config)
    with pytest.raises(InsufficientShardsError):
        decoder.decode(encoded.shards[:2], original_size=3, config=config)


def test_decoder_rejects_duplicate_shard_indexes(encoder, decoder):
    config = ErasureCodingConfig(2, 4)
    encoded = encoder.encode(make_payload(b"abc"), config=config)
    with pytest.raises(ErasureCodingError, match="unique"):
        decoder.decode(
            (encoded.shards[0], encoded.shards[0]),
            original_size=3,
            config=config,
        )


def test_decoder_rejects_out_of_range_index(encoder, decoder):
    config = ErasureCodingConfig(2, 4)
    encoded = encoder.encode(make_payload(b"abc"), config=config)
    forged = EncodedShard(
        index=4,
        data=encoded.shards[0].data,
        sha256=encoded.shards[0].sha256,
    )
    with pytest.raises(ErasureCodingError, match="outside"):
        decoder.decode(
            (forged, encoded.shards[1]),
            original_size=3,
            config=config,
        )


def test_shard_artifact_rejects_wrong_checksum(encoder):
    config = ErasureCodingConfig(2, 4)
    encoded = encoder.encode(make_payload(b"abc"), config=config)
    with pytest.raises(InvalidArtifactError, match="sha256"):
        replace(encoded.shards[0], data=b"tampered")


def test_decoder_rechecks_shard_integrity(encoder, decoder):
    config = ErasureCodingConfig(2, 4)
    encoded = encoder.encode(make_payload(b"abc"), config=config)
    corrupted = object.__new__(EncodedShard)
    object.__setattr__(corrupted, "index", encoded.shards[0].index)
    object.__setattr__(corrupted, "data", b"tampered")
    object.__setattr__(corrupted, "sha256", encoded.shards[0].sha256)
    with pytest.raises(ShardIntegrityError):
        decoder.decode(
            (corrupted, encoded.shards[1]),
            original_size=3,
            config=config,
        )


def test_decoder_rejects_invalid_original_size(encoder, decoder):
    config = ErasureCodingConfig(2, 4)
    encoded = encoder.encode(make_payload(b"abc"), config=config)
    with pytest.raises(ErasureCodingError):
        decoder.decode(encoded.shards[:2], original_size=-1, config=config)
    with pytest.raises(ErasureCodingError):
        decoder.decode(encoded.shards[:2], original_size=True, config=config)


def test_encoder_requires_generation_payload(encoder):
    with pytest.raises(InvalidArtifactError):
        encoder.encode(b"not an artifact", config=ErasureCodingConfig(2, 4))


def test_decoder_requires_tuple(encoder, decoder):
    config = ErasureCodingConfig(2, 4)
    encoded = encoder.encode(make_payload(b"abc"), config=config)
    with pytest.raises(ErasureCodingError, match="tuple"):
        decoder.decode(
            list(encoded.shards[:2]),
            original_size=3,
            config=config,
        )
