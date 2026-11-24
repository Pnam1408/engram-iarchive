from uuid import uuid4
import pytest
from engram.metadata.mmr.models import MMRNodeRecord



def test_node_record_accepts_aligned_coordinate():
    rec = MMRNodeRecord(str(uuid4()), 8, 3, b"x" * 32)
    assert rec.height == 3


@pytest.mark.parametrize("start,height", [(3, 2), (2, 2), (-1, 1)])
def test_node_record_rejects_invalid_alignment_or_start(start, height):
    with pytest.raises(ValueError):
        MMRNodeRecord(str(uuid4()), start, height, b"x" * 32)


def test_node_record_rejects_leaf_height():
    with pytest.raises(ValueError, match="height"):
        MMRNodeRecord(str(uuid4()), 0, 0, b"x" * 32)


def test_node_record_rejects_wrong_hash_size():
    with pytest.raises(ValueError, match="32 bytes"):
        MMRNodeRecord(str(uuid4()), 0, 1, b"x")
