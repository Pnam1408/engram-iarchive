#engram/incremental/challenge.py
from dataclasses import dataclass
import hashlib
import random

from engram.challenge_common import derive_p

DEFAULT_K = 3

@dataclass(frozen=True)
class GenerationLayout:
    """Describe the shard/chunk layout of one generation."""

    generation_id: str
    chunks_per_shard: tuple[int, ...]

    @property
    def total_shards(self) -> int:
        return len(self.chunks_per_shard)


@dataclass(frozen=True)
class ChallengedShard:
    """One challenged shard and one selected chunk within it."""

    generation_id: str
    shard_index: int
    chunk_index: int


@dataclass(frozen=True)
class IncrementalChallenge:
    """Challenge containing selected shards across generations."""

    p: bytes
    challenged_shards: tuple[ChallengedShard, ...]


def _derive_seed(
    p: bytes,
    domain: bytes,
    *indices: int,
) -> int:
    """Derive an independent deterministic seed with domain separation."""
    payload = domain + p

    for index in indices:
        if (
            not isinstance(index, int)
            or isinstance(index, bool)
            or index < 0
        ):
            raise ValueError("Indices must be non-negative integers")

        payload += index.to_bytes(8, "big")

    return int.from_bytes(
        hashlib.sha256(payload).digest(),
        "big",
    )


def _validate_layouts(
    layouts: tuple[GenerationLayout, ...],
) -> None:
    if not isinstance(layouts, tuple) or not layouts:
        raise ValueError("layouts must be a non-empty tuple")

    generation_ids = set()

    for layout in layouts:
        if not isinstance(layout, GenerationLayout):
            raise TypeError("Every layout must be a GenerationLayout")

        if not layout.generation_id:
            raise ValueError("generation_id must not be empty")

        if layout.generation_id in generation_ids:
            raise ValueError("generation_id values must be unique")

        generation_ids.add(layout.generation_id)

        if not isinstance(layout.chunks_per_shard, tuple):
            raise TypeError("chunks_per_shard must be a tuple")

        if not layout.chunks_per_shard:
            raise ValueError(
                f"Generation {layout.generation_id} has no shards"
            )

        for count in layout.chunks_per_shard:
            if (
                not isinstance(count, int)
                or isinstance(count, bool)
                or count <= 0
            ):
                raise ValueError(
                    "Every shard must contain at least one chunk"
                )


def select_generation_indices(
    p: bytes,
    total_generations: int,
    k: int = DEFAULT_K,
) -> tuple[int, ...]:
    """
    Select up to k distinct generations deterministically.

    If there are fewer than k generations, all generations are selected.
    """
    if not isinstance(p, bytes) or len(p) != 32:
        raise ValueError("p must be a 32-byte SHA-256 digest")

    if (
        not isinstance(total_generations, int)
        or isinstance(total_generations, bool)
        or total_generations <= 0
    ):
        raise ValueError("total_generations must be positive")

    if not isinstance(k, int) or isinstance(k, bool) or k <= 0:
        raise ValueError("k must be positive")

    selected_count = min(k, total_generations)

    seed = _derive_seed(
        p,
        b"ENGRAM_SELECT_GENERATIONS_V1",
    )

    rng = random.Random(seed)

    return tuple(
        sorted(rng.sample(range(total_generations), selected_count))
    )


def select_shard_indices(
    p: bytes,
    generation_index: int,
    total_shards: int,
    k: int,
) -> tuple[int, ...]:
    """
    Select shards deterministically within one generation.

    The generation index is included in the seed, so different
    generations receive independent selections.
    """
    if not isinstance(p, bytes) or len(p) != 32:
        raise ValueError("p must be a 32-byte SHA-256 digest")

    if (
        not isinstance(total_shards, int)
        or isinstance(total_shards, bool)
        or total_shards <= 0
    ):
        raise ValueError("total_shards must be positive")

    if (
        not isinstance(k, int)
        or isinstance(k, bool)
        or not 1 <= k <= total_shards
    ):
        raise ValueError("k must be between 1 and total_shards")

    seed = _derive_seed(
        p,
        b"ENGRAM_SELECT_SHARDS_V1",
        generation_index,
    )

    rng = random.Random(seed)

    return tuple(
        sorted(rng.sample(range(total_shards), k))
    )


def select_chunk_index(
    p: bytes,
    generation_index: int,
    shard_index: int,
    chunks_per_shard: tuple[int, ...],
) -> int:
    """Select one chunk deterministically within a selected shard."""
    if not isinstance(p, bytes) or len(p) != 32:
        raise ValueError("p must be a 32-byte SHA-256 digest")

    if not chunks_per_shard:
        raise ValueError("chunks_per_shard must not be empty")

    if (
        not isinstance(shard_index, int)
        or isinstance(shard_index, bool)
        or not 0 <= shard_index < len(chunks_per_shard)
    ):
        raise ValueError("shard_index is out of range")

    total_chunks = chunks_per_shard[shard_index]

    if (
        not isinstance(total_chunks, int)
        or isinstance(total_chunks, bool)
        or total_chunks <= 0
    ):
        raise ValueError("The selected shard must contain chunks")

    seed = _derive_seed(
        p,
        b"ENGRAM_SELECT_CHUNK_V1",
        generation_index,
        shard_index,
    )

    rng = random.Random(seed)

    return rng.randrange(total_chunks)


def create_incremental_challenge(
    layouts: tuple[GenerationLayout, ...],
    k: int = DEFAULT_K,
    block_hash: str = "sample-block-hash",
    miner_id: str = "miner-001",
    epoch_id: str = "epoch-001",
) -> IncrementalChallenge:
    """
    Create one challenge across multiple generations.

    Selection policy:
      1. Select up to k distinct generations.
      2. Select one shard from each selected generation.
      3. Select one chunk within each selected shard.

    If k is greater than the number of generations, additional
    shards are selected from the remaining capacity of generations.
    """
    _validate_layouts(layouts)

    if not isinstance(k, int) or isinstance(k, bool) or k <= 0:
        raise ValueError("k must be positive")

    p = derive_p(block_hash, miner_id, epoch_id)

    selected_generation_indices = select_generation_indices(
        p=p,
        total_generations=len(layouts),
        k=k,
    )

    selected_shards: dict[int, list[int]] = {
        generation_index: []
        for generation_index in selected_generation_indices
    }

    # First select one shard from each selected generation.
    for generation_index in selected_generation_indices:
        layout = layouts[generation_index]

        selected_shards[generation_index].extend(
            select_shard_indices(
                p=p,
                generation_index=generation_index,
                total_shards=layout.total_shards,
                k=1,
            )
        )

    # If k exceeds the number of generations, distribute the
    # remaining selections among generations with unused shards.
    remaining = k - len(selected_generation_indices)

    while remaining > 0:
        candidates = [
            index
            for index in selected_generation_indices
            if len(selected_shards[index])
            < layouts[index].total_shards
        ]

        if not candidates:
            break

        round_seed = _derive_seed(
            p,
            b"ENGRAM_EXTRA_SHARD_ROUND_V1",
            remaining,
        )
        rng = random.Random(round_seed)
        generation_index = candidates[
            rng.randrange(len(candidates))
        ]

        layout = layouts[generation_index]
        already_selected = set(selected_shards[generation_index])

        available = [
            shard_index
            for shard_index in range(layout.total_shards)
            if shard_index not in already_selected
        ]

        shard_seed = _derive_seed(
            p,
            b"ENGRAM_EXTRA_SHARD_V1",
            generation_index,
            remaining,
        )
        shard_rng = random.Random(shard_seed)
        shard_index = shard_rng.choice(available)

        selected_shards[generation_index].append(shard_index)
        remaining -= 1

    challenged_shards = []

    # Build the final ordered list: generation -> shard -> chunk.
    for generation_index in selected_generation_indices:
        layout = layouts[generation_index]

        for shard_index in sorted(selected_shards[generation_index]):
            chunk_index = select_chunk_index(
                p=p,
                generation_index=generation_index,
                shard_index=shard_index,
                chunks_per_shard=layout.chunks_per_shard,
            )

            challenged_shards.append(
                ChallengedShard(
                    generation_id=layout.generation_id,
                    shard_index=shard_index,
                    chunk_index=chunk_index,
                )
            )

    return IncrementalChallenge(
        p=p,
        challenged_shards=tuple(challenged_shards),
    )
