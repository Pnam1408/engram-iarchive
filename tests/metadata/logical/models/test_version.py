from uuid import uuid4

import pytest

from engram.metadata.logical.models import Version


def make_version(**overrides):
    values = {
        "version_id": str(uuid4()),
        "object_id": "object-001",
        "sequence": 1,
        "parent_version_id": None,
        "delta_root": b"\x01" * 32,
        "previous_version_hash": None,
        "codec": "engram-v1",
        "writer_public_key": b"public-key",
        "signature": b"signature",
        "created_at": 1000,
    }
    values.update(overrides)
    return Version(**values)


def test_create_first_version():
    version = make_version()

    assert version.sequence == 1
    assert version.parent_version_id is None
    assert version.previous_version_hash is None


def test_reject_first_version_with_parent():
    with pytest.raises(ValueError):
        make_version(parent_version_id=str(uuid4()))


def test_reject_first_version_with_previous_hash():
    with pytest.raises(ValueError):
        make_version(previous_version_hash=b"\x01" * 32)


def test_create_non_first_version():
    version = make_version(
        sequence=2,
        parent_version_id=str(uuid4()),
        previous_version_hash=b"\x02" * 32,
    )

    assert version.sequence == 2
    assert version.previous_version_hash == b"\x02" * 32


def test_reject_non_first_version_without_parent():
    with pytest.raises(ValueError):
        make_version(
            sequence=2,
            previous_version_hash=b"\x02" * 32,
        )


def test_reject_non_first_version_without_previous_hash():
    with pytest.raises(ValueError):
        make_version(
            sequence=2,
            parent_version_id=str(uuid4()),
        )
