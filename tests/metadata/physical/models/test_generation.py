from uuid import uuid4

import pytest

from engram.metadata.physical.models import Generation, GenerationStatus


def make_generation(**overrides):
    values = {
        "generation_id": str(uuid4()),
        "data_shards": 4,
        "total_shards": 8,
        "status": GenerationStatus.OPEN,
        "created_at": 100,
        "updated_at": 100,
    }
    values.update(overrides)
    return Generation(**values)


def test_open_generation_round_trip_values():
    generation = make_generation()
    assert generation.status is GenerationStatus.OPEN
    assert generation.sealed_at is None
    assert generation.encoded_at is None


def test_generation_requires_valid_erasure_coding_parameters():
    with pytest.raises(ValueError, match="total_shards"):
        make_generation(data_shards=5, total_shards=4)
    with pytest.raises(ValueError, match="positive"):
        make_generation(data_shards=0)


def test_generation_requires_sealed_timestamp_after_open():
    with pytest.raises(ValueError, match="sealed_at"):
        make_generation(status=GenerationStatus.SEALED)


def test_encoded_generation_requires_both_lifecycle_timestamps():
    with pytest.raises(ValueError, match="sealed_at"):
        make_generation(status=GenerationStatus.ENCODED, encoded_at=120, updated_at=120)
    with pytest.raises(ValueError, match="encoded_at"):
        make_generation(status=GenerationStatus.ENCODED, sealed_at=110, updated_at=110)


def test_encoded_generation_accepts_consistent_timestamps():
    generation = make_generation(
        status=GenerationStatus.ENCODED,
        sealed_at=110,
        encoded_at=120,
        updated_at=120,
    )
    assert generation.encoded_at == 120


def test_failed_generation_may_fail_before_sealing():
    generation = make_generation(status=GenerationStatus.FAILED, updated_at=105)
    assert generation.sealed_at is None


def test_generation_rejects_boolean_integer_fields():
    with pytest.raises(TypeError):
        make_generation(data_shards=True)


def test_generation_cannot_be_encoded_before_encoding_completes():
    with pytest.raises(ValueError, match="before encoding completes"):
        make_generation(
            status=GenerationStatus.SEALED,
            sealed_at=110,
            encoded_at=120,
            updated_at=120,
        )
