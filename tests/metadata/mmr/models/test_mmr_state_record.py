from uuid import uuid4
import pytest
from engram.commitment.mmr import MMRState
from engram.metadata.mmr.models import MMRStateRecord, MMR_PROTOCOL


def test_state_record_accepts_valid_state():
    record = MMRStateRecord(str(uuid4()), MMR_PROTOCOL, MMRState(0, ()))
    assert record.state.leaf_count == 0


def test_state_record_rejects_wrong_protocol():
    with pytest.raises(ValueError, match="unsupported"):
        MMRStateRecord(str(uuid4()), "wrong", MMRState(0, ()))


def test_state_record_rejects_invalid_object_id():
    with pytest.raises(ValueError, match="valid UUID"):
        MMRStateRecord("not-a-uuid", MMR_PROTOCOL, MMRState(0, ()))
