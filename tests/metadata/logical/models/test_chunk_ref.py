from uuid import uuid4

import pytest

from engram.metadata.logical.models import ChunkRef


def test_create_chunk_ref():
    ref = ChunkRef(
        chunk_ref_id=str(uuid4()),
        chunk_id=str(uuid4()),
        order_key="a0",
    )

    assert ref.order_key == "a0"


def test_reject_invalid_chunk_ref_id():
    with pytest.raises(ValueError):
        ChunkRef(
            chunk_ref_id="invalid",
            chunk_id=str(uuid4()),
            order_key="a0",
        )


def test_reject_invalid_order_key():
    with pytest.raises(ValueError):
        ChunkRef(
            chunk_ref_id=str(uuid4()),
            chunk_id=str(uuid4()),
            order_key="",
        )
