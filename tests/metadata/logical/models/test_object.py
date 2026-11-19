from uuid import uuid4

import pytest

from engram.metadata.logical.models import Object


def test_create_new_object():
    obj = Object(
        object_id="object-001",
        created_at=1000,
        updated_at=1000,
    )

    assert obj.head_sequence == 0
    assert obj.head_version_id is None


def test_object_with_head_version():
    version_id = str(uuid4())

    obj = Object(
        object_id="object-001",
        head_sequence=1,
        head_version_id=version_id,
        created_at=1000,
        updated_at=2000,
    )

    assert obj.head_sequence == 1
    assert obj.head_version_id == version_id


def test_reject_head_sequence_without_version_id():
    with pytest.raises(ValueError):
        Object(
            object_id="object-001",
            head_sequence=1,
            created_at=1000,
            updated_at=1000,
        )


def test_reject_version_id_without_head_sequence():
    with pytest.raises(ValueError):
        Object(
            object_id="object-001",
            head_version_id=str(uuid4()),
            created_at=1000,
            updated_at=1000,
        )


def test_reject_updated_at_before_created_at():
    with pytest.raises(ValueError):
        Object(
            object_id="object-001",
            created_at=2000,
            updated_at=1000,
        )
