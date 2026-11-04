#engram/challenge_common.py
import hashlib


def derive_p(
    block_hash: str,
    miner_id: str,
    epoch_id: str,
) -> bytes:
    payload = (
        b"ENGRAM_CHALLENGE_V1"
        + _encode_field(block_hash)
        + _encode_field(miner_id)
        + _encode_field(epoch_id)
    )
    return hashlib.sha256(payload).digest()


def _encode_field(value: str) -> bytes:
    data = value.encode("utf-8")
    return len(data).to_bytes(4, "big") + data