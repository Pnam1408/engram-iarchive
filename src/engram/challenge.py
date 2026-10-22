
from dataclasses import dataclass
import hashlib
import random


DEFAULT_K = 3


@dataclass(frozen=True)
class Challenge:
    chunk_id: str
    p: bytes
    shard_indices: tuple[int, ...]
    chunk_indices: tuple[int, ...]


def _encode_field(value: str) -> bytes:
    """Encode one challenge input unambiguously."""
    data = value.encode("utf-8")
    return len(data).to_bytes(4, "big") + data


def derive_p(
    block_hash: str,
    miner_id: str,
    epoch_id: str,
) -> bytes:
    """Derive a deterministic challenge value using SHA-256."""
    payload = (
        b"ENGRAM_CHALLENGE_V1"
        + _encode_field(block_hash)
        + _encode_field(miner_id)
        + _encode_field(epoch_id)
    )
    return hashlib.sha256(payload).digest()


def select_shard_indices(
    p: bytes,
    total_shards: int,
    k: int = DEFAULT_K,
) -> tuple[int, ...]:
    """Select k distinct shard indices deterministically from p."""
    if not isinstance(p, bytes) or len(p) != 32:
        raise ValueError("p must be a 32-byte SHA-256 digest")

    if total_shards <= 0:
        raise ValueError("total_shards must be positive")

    if not 1 <= k <= total_shards:
        raise ValueError("k must be between 1 and total_shards")

    rng = random.Random(int.from_bytes(p, "big"))
    return tuple(sorted(rng.sample(range(total_shards), k)))


def select_chunk_indices(
    p: bytes,
    shard_indices: tuple[int, ...],
    chunks_per_shard: tuple[int, ...],
) -> tuple[int, ...]:
    """
    Select one chunk index for each selected shard.

    chunks_per_shard[i] is the number of chunks in shard i.
    The same p and layout always produce the same indices.
    """
    if not isinstance(p, bytes) or len(p) != 32:
        raise ValueError("p must be a 32-byte SHA-256 digest")

    if not chunks_per_shard:
        raise ValueError("chunks_per_shard must not be empty")

    if any(n <= 0 for n in chunks_per_shard):
        raise ValueError("Every shard must contain at least one chunk")

    if len(set(shard_indices)) != len(shard_indices):
        raise ValueError("shard_indices must be distinct")

    if any(i < 0 or i >= len(chunks_per_shard) for i in shard_indices):
        raise ValueError("A shard index is out of range")

    chunk_indices = []

    for shard_index in shard_indices:
        n_chunks = chunks_per_shard[shard_index]

        # Derive an independent deterministic seed for each shard.
        payload = (
            b"ENGRAM_CHUNK_CHALLENGE_V1"
            + p
            + shard_index.to_bytes(4, "big")
        )
        seed = int.from_bytes(
            hashlib.sha256(payload).digest(), "big"
        )

        rng = random.Random(seed)
        chunk_indices.append(rng.randrange(n_chunks))

    return tuple(chunk_indices)


def create_challenge(
    chunk_id: str,
    total_shards: int,
    chunks_per_shard: tuple[int, ...],
    k: int = DEFAULT_K,
    block_hash: str = "sample-block-hash",
    miner_id: str = "miner-001",
    epoch_id: str = "epoch-001",
) -> Challenge:
    if len(chunks_per_shard) != total_shards:
        raise ValueError(
            "chunks_per_shard length must equal total_shards"
        )

    p = derive_p(block_hash, miner_id, epoch_id)

    shard_indices = select_shard_indices(
        p=p,
        total_shards=total_shards,
        k=k,
    )

    chunk_indices = select_chunk_indices(
        p=p,
        shard_indices=shard_indices,
        chunks_per_shard=chunks_per_shard,
    )

    return Challenge(
        chunk_id=chunk_id,
        p=p,
        shard_indices=shard_indices,
        chunk_indices=chunk_indices,
    )
