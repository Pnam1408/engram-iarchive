import sys
import types

import pytest

from engram.commitment.common.exceptions import (
    CommitmentError,
    InvalidTypeError,
    ValueOutOfRangeError,
)
from engram.commitment.shard_merkle.hashing import (
    CHUNK_BYTES,
    FIELD_MODULUS,
    hash_chunk,
    hash_parent,
    poseidon_hash_3,
)


@pytest.fixture
def fake_backend(monkeypatch):
    calls = []

    def hash_elements(values):
        calls.append(tuple(map(int, values)))
        # Deterministic test double only; deliberately not a Poseidon implementation.
        return str(
            (sum((i + 1) * int(v) for i, v in enumerate(values))) % FIELD_MODULUS
        )

    monkeypatch.setitem(
        sys.modules, "fast_poseidon", types.SimpleNamespace(hash_elements=hash_elements)
    )
    return calls


def test_poseidon_adapter_validates_and_calls_three_inputs(fake_backend):
    result = poseidon_hash_3(1, 2, 3)
    assert result == 14
    assert fake_backend == [(1, 2, 3)]
    assert 0 <= result < FIELD_MODULUS


@pytest.mark.parametrize("value", [True, False, 1.0, "1", None])
def test_poseidon_rejects_non_integer_inputs(value):
    with pytest.raises(InvalidTypeError):
        poseidon_hash_3(value, 2, 3)


@pytest.mark.parametrize("value", [-1, FIELD_MODULUS, FIELD_MODULUS + 1])
def test_poseidon_rejects_noncanonical_field_inputs(value):
    with pytest.raises(ValueOutOfRangeError):
        poseidon_hash_3(value, 2, 3)


def test_chunk_hash_binds_length_and_accepts_boundary_sizes(fake_backend):
    assert isinstance(hash_chunk(b"x"), int)
    assert isinstance(hash_chunk(b"x" * CHUNK_BYTES), int)
    assert hash_chunk(b"\x00\x01") != hash_chunk(b"\x01")
    assert hash_chunk(b"\x00\x01") == hash_chunk(b"\x00\x01")


@pytest.mark.parametrize("data", [b"", b"x" * 32])
def test_chunk_hash_rejects_invalid_lengths(data):
    with pytest.raises(ValueOutOfRangeError):
        hash_chunk(data)


@pytest.mark.parametrize("data", [bytearray(b"x"), memoryview(b"x"), "x"])
def test_chunk_hash_requires_exact_bytes(data):
    with pytest.raises(InvalidTypeError):
        hash_chunk(data)


def test_parent_hash_is_ordered_and_validates_children(fake_backend):
    assert hash_parent(1, 2) != hash_parent(2, 1)
    with pytest.raises(InvalidTypeError):
        hash_parent(True, 2)
    with pytest.raises(ValueOutOfRangeError):
        hash_parent(0, FIELD_MODULUS)


def test_backend_errors_are_wrapped(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "fast_poseidon",
        types.SimpleNamespace(
            hash_elements=lambda values: (_ for _ in ()).throw(
                ValueError("bad backend")
            )
        ),
    )
    with pytest.raises(CommitmentError):
        poseidon_hash_3(1, 2, 3)


def test_backend_output_is_validated(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "fast_poseidon",
        types.SimpleNamespace(hash_elements=lambda values: str(FIELD_MODULUS)),
    )
    with pytest.raises(CommitmentError):
        poseidon_hash_3(1, 2, 3)


def test_circom_compatible():
    result = poseidon_hash_3(11, 22, 2026)
    assert (
        result
        == 4108572264724763218754967574827674763137797405379838691109633427075516621983
    )
