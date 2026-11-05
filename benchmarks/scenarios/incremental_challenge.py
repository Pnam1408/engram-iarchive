from __future__ import annotations

from typing import Any

from benchmarks.common.timing import measure_ms

from benchmarks.scenarios.incremental_instrumentation import TimedIncrementalStore
from engram.incremental.challenge import (
    GenerationLayout,
    create_incremental_challenge,
)
from engram.proof import (
    ShardChallenge,
    ShardWitness,
    generate_shard_proof,
    verify_challenge_proofs,
)


def benchmark_challenge(
    store: TimedIncrementalStore,
    object_id: str,
    k: int = 3,
) -> dict[str, Any]:
    """Benchmark incremental challenge generation and verification."""

    state = store._load(object_id)
    generations = state.get("generations", [])

    # ---------------------------------------------------------
    # 1. Prepare trusted generation metadata (not timed).
    # ---------------------------------------------------------

    layouts: list[GenerationLayout] = []
    registered_roots: dict[str, int] = {}
    verification_layouts: dict[str, tuple[int, ...]] = {}

    for generation in generations:
        generation_id = generation["generation_id"]

        if generation["status"] != "Available":
            continue

        counts = store.storage.get_chunk_counts(generation_id)
        root = store.storage.get_merkle_root(generation_id)

        layouts.append(
            GenerationLayout(
                generation_id=generation_id,
                chunks_per_shard=counts,
            )
        )

        registered_roots[generation_id] = root
        verification_layouts[generation_id] = counts

    if not layouts:
        raise RuntimeError("No available generations to challenge")

    layout_tuple = tuple(layouts)

    # ---------------------------------------------------------
    # 2. Challenge generation.
    # ---------------------------------------------------------

    challenge, challenge_generation_ms = measure_ms(
        create_incremental_challenge,
        layout_tuple,
        k,
    )

    targets = tuple(
        ShardChallenge(
            commitment_id=item.generation_id,
            shard_index=item.shard_index,
            chunk_index=item.chunk_index,
        )
        for item in challenge.challenged_shards
    )

    # ---------------------------------------------------------
    # 3. Witness retrieval.
    # ---------------------------------------------------------

    witnesses: list[ShardWitness] = []
    witness_retrieval_ms = 0.0

    for target in targets:
        response, elapsed_ms = measure_ms(
            store.storage.get_chunk_response,
            target.commitment_id,
            target.shard_index,
            target.chunk_index,
        )

        chunk_data, chunk_path, shard_path = response

        witnesses.append(
            ShardWitness(
                commitment_id=target.commitment_id,
                shard_index=target.shard_index,
                chunk_index=target.chunk_index,
                chunk_data=chunk_data,
                chunk_merkle_path=chunk_path,
                shard_merkle_path=shard_path,
            )
        )

        witness_retrieval_ms += elapsed_ms

    # ---------------------------------------------------------
    # 4. Proof construction.
    # ---------------------------------------------------------

    proofs = []
    proof_construction_ms = 0.0

    for target, witness in zip(targets, witnesses):
        proof, elapsed_ms = measure_ms(
            generate_shard_proof,
            target,
            challenge.p,
            witness,
        )

        proofs.append(proof)
        proof_construction_ms += elapsed_ms

    proof_tuple = tuple(proofs)

    # ---------------------------------------------------------
    # 5. Challenge verification.
    # ---------------------------------------------------------

    verification_result, challenge_verification_ms = measure_ms(
        verify_challenge_proofs,
        targets,
        proof_tuple,
        registered_roots,
        challenge.p,
        verification_layouts,
    )

    if not verification_result.valid:
        raise RuntimeError("Challenge verification failed")

    return {
        "challenge_generation_ms": challenge_generation_ms,
        "witness_retrieval_ms": witness_retrieval_ms,
        "proof_construction_ms": proof_construction_ms,
        "challenge_verification_ms": challenge_verification_ms,
        "challenge_total_ms": (
            challenge_generation_ms
            + witness_retrieval_ms
            + proof_construction_ms
            + challenge_verification_ms
        ),
        "generation_count": len(layouts),
        "challenged_shard_count": len(targets),
        "verified_proof_count": verification_result.verified_count,
        "challenge_valid": verification_result.valid,
    }