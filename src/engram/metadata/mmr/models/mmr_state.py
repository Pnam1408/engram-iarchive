"""Persistable metadata record for an object's Version History MMR state."""

from dataclasses import dataclass
from engram.commitment.mmr import MMRState, validate_mmr_state

MMR_PROTOCOL = "ENGRAM-VERSION-MMR/1"


def _validate_object_id(value: str) -> None:
    if not isinstance(value, str):
        raise TypeError("object_id must be a string")
    if not value:
        raise ValueError("object_id must not be empty")


@dataclass(frozen=True, slots=True)
class MMRStateRecord:
    object_id: str
    protocol: str
    state: MMRState

    def __post_init__(self) -> None:
        _validate_object_id(self.object_id)
        if not isinstance(self.protocol, str) or not self.protocol:
            raise ValueError("protocol must be a non-empty string")
        if self.protocol != MMR_PROTOCOL:
            raise ValueError("unsupported MMR protocol")
        validate_mmr_state(self.state)
