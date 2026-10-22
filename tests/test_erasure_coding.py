
import pytest

from engram.erasure_coding import encode, decode


def test_encode_decode_all_shards():
    original = b"Engram incremental storage simulation"

    encoded = encode(original, data_shards=4, total_shards=6)

    available = {
        i: shard for i, shard in enumerate(encoded.shards)
    }

    restored = decode(
        available,
        original_size=encoded.original_size,
        data_shards=encoded.data_shards,
        total_shards=encoded.total_shards,
    )

    assert restored == original


def test_recover_when_two_shards_are_missing():
    original = b"A longer test message for erasure coding."

    encoded = encode(original, data_shards=4, total_shards=6)

    # Mất shard 1 và shard 4.
    available = {
        i: shard
        for i, shard in enumerate(encoded.shards)
        if i not in {1, 4}
    }

    restored = decode(
        available,
        original_size=encoded.original_size,
        data_shards=4,
        total_shards=6,
    )

    assert restored == original


def test_not_enough_shards():
    encoded = encode(b"test data", data_shards=4, total_shards=6)

    available = {
        i: encoded.shards[i] for i in range(3)
    }

    with pytest.raises(ValueError, match="Not enough shards"):
        decode(
            available,
            original_size=encoded.original_size,
            data_shards=4,
            total_shards=6,
        )


def test_empty_data():
    encoded = encode(b"", data_shards=4, total_shards=6)

    available = {
        i: encoded.shards[i] for i in [0, 2, 4, 5]
    }

    restored = decode(
        available,
        original_size=encoded.original_size,
        data_shards=4,
        total_shards=6,
    )

    assert restored == b""
