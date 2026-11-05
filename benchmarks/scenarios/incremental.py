#benchmarks/scenarios/incremental.py
"""B1 incremental-storage benchmark for ENGRAM-iARCHIVE.

Each append adds only a delta. Deltas are grouped into generations according
to --batch-size. A generation is sealed once its batch is complete.

Groth16 proving is not included; it is benchmarked separately.
Run from the repository root:
    python -m benchmarks.scenarios.incremental \
        --initial-size-mib 1 --delta-size-mib 0.1 --updates 5 \
        --batch-size 2 --repeat 3 --warmup 1
"""
from __future__ import annotations

import argparse
import math
import platform
import statistics
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from benchmarks.common.results import export_results
from benchmarks.common.statistics import numeric_summary
from benchmarks.common.timing import measure_ms
from benchmarks.common.workload import generate_update_workload
from benchmarks.scenarios.incremental_challenge import benchmark_challenge
from benchmarks.scenarios.incremental_instrumentation import (
    TimedIncrementalStore,
    STAGE_NAMES,
)

MIB = 1024 * 1024


def _validate_args(args: argparse.Namespace) -> None:
    for name in ("initial_size_mib", "delta_size_mib"):
        value = getattr(args, name)
        if not math.isfinite(value) or value <= 0:
            raise ValueError(
                f"--{name.replace('_', '-')} must be a finite number > 0"
            )

    if args.updates < 0:
        raise ValueError("--updates must be >= 0")
    if args.batch_size < 1:
        raise ValueError("--batch-size must be >= 1")
    if args.repeat < 1:
        raise ValueError("--repeat must be >= 1")
    if args.warmup < 0:
        raise ValueError("--warmup must be >= 0")
    if not (1 <= args.data_shards < args.total_shards <= 255):
        raise ValueError(
            "Require 1 <= data-shards < total-shards <= 255"
        )
    if args.chunk_size <= 0:
        raise ValueError("--chunk-size must be > 0")


def _build_expected_snapshots(
    initial_data: bytes,
    deltas: Sequence[bytes],
) -> list[bytes]:
    """Build expected object contents for logical versions 0..N."""
    snapshots = [initial_data]
    current = initial_data

    for delta in deltas:
        current += delta
        snapshots.append(current)

    return snapshots


def _summarize_rows(
    rows: Sequence[Mapping[str, Any]],
    group_field: str,
) -> list[dict[str, Any]]:
    """Create a numeric summary for each value of group_field."""
    groups = sorted({int(row[group_field]) for row in rows})
    output: list[dict[str, Any]] = []

    excluded = {
        "run_index",
        "logical_version",
        "store_version",
        "delta_size_bytes",
        "append_size_bytes",
        "snapshot_size_bytes",
        "batch_size",
        "data_shards",
        "total_shards",
    }

    for group_value in groups:
        group = [
            row for row in rows
            if int(row[group_field]) == group_value
        ]

        numeric_rows = [
            {
                key: value
                for key, value in row.items()
                if key not in excluded
                and isinstance(value, (int, float))
                and not isinstance(value, bool)
            }
            for row in group
        ]

        summary: dict[str, Any] = {
            group_field: group_value,
            "runs": len(group),
        }
        summary.update(numeric_summary(numeric_rows))
        output.append(summary)

    return output

def measure_seal(
    store: TimedIncrementalStore,
    object_id: str,
    run_index: int,
    generation_index: int,
    stage_rows: list[dict[str, Any]],
) -> tuple[bool, float]:
    store.reset_stage_timings()

    sealed, seal_ms = measure_ms(store.seal, object_id)

    if not sealed:
        return False, seal_ms

    timings = store.stage_timings.copy()

    measured_stage_ms = sum(
        timings.get(name, 0.0)
        for name in STAGE_NAMES
    )

    stage_rows.append({
        "scenario": "incremental",
        "run_index": run_index,
        "generation_index": generation_index,
        **{
            f"{name}_ms": timings.get(name, 0.0)
            for name in STAGE_NAMES
        },
        "seal_total_ms": seal_ms,
        "seal_unattributed_ms": max(
            0.0, seal_ms - measured_stage_ms
        ),
    })

    return True, seal_ms

def run_sequence(
    initial_data: bytes,
    deltas: Sequence[bytes],
    run_index: int,
    args: argparse.Namespace,
) -> tuple[
        list[dict[str, Any]], # version_rows
        list[dict[str, Any]], # generation_rows
        list[dict[str, Any]], # stage_rows
        dict[str, Any], # sequence_row
        dict[str, Any], # challenge_row
    ]:
    """Execute one independent incremental-storage sequence."""
    object_id = f"benchmark-object-{uuid.uuid4().hex[:12]}"
    expected_snapshots = _build_expected_snapshots(initial_data, deltas)

    version_rows: list[dict[str, Any]] = []
    generation_rows: list[dict[str, Any]] = []
    stage_rows: list[dict[str, Any]] = []

    # A unique temporary root isolates every measured sequence.
    with tempfile.TemporaryDirectory(
        prefix="engram_incremental_"
    ) as temp_dir:
        store = TimedIncrementalStore(
            Path(temp_dir) / "store",
            data_shards=args.data_shards,
            total_shards=args.total_shards,
            chunk_size=args.chunk_size,
            # Prevent automatic size-based sealing. The benchmark controls
            # generation boundaries explicitly using batch_size.
            size_threshold=len(initial_data) + sum(map(len, deltas)) + 1,
            # Prevent the max-wait rule from sealing during this experiment.
            max_wait_seconds=1e12,
        )

        sequence_start = time.perf_counter()
        total_append_ms = 0.0
        total_seal_ms = 0.0
        total_retrieval_ms = 0.0
        total_generation_plaintext_bytes = 0
        sealed_generation_count = 0

        # The initial object is represented by the first Store version.
        # Store version 1 maps to logical benchmark version 0.
        append_record, append_ms = measure_ms(
            store.append, object_id, initial_data
        )
        total_append_ms += append_ms

        initial_seal_ms = 0.0
        sealed, initial_seal_ms = measure_seal(
            store,
            object_id,
            run_index,
            generation_index=0,
            stage_rows=stage_rows,
        )
        if not sealed:
            raise RuntimeError("Initial generation was not sealed")

        total_seal_ms += initial_seal_ms
        sealed_generation_count += 1
        total_generation_plaintext_bytes += len(initial_data)

        version_rows.append({
            "scenario": "incremental",
            "run_index": run_index,
            "logical_version": 0,
            "store_version": int(append_record["version"]),
            "delta_size_bytes": 0,
            "append_size_bytes": len(initial_data),
            "snapshot_size_bytes": len(expected_snapshots[0]),
            "append_ms": append_ms,
            "seal_ms": initial_seal_ms,
            "write_total_ms": append_ms + initial_seal_ms,
            "append_status": append_record.get("status"),
            "generation_sealed": True,
            "data_shards": args.data_shards,
            "total_shards": args.total_shards,
            "batch_size": args.batch_size,
        })

        # Append deltas and seal after every complete batch.
        for delta_index, delta in enumerate(deltas, start=1):
            record, append_ms = measure_ms(
                store.append, object_id, delta
            )
            total_append_ms += append_ms

            is_batch_end = (
                delta_index % args.batch_size == 0
                or delta_index == len(deltas)
            )

            seal_ms = 0.0
            if is_batch_end:
                sealed, seal_ms = measure_seal(
                    store,
                    object_id,
                    run_index,
                    generation_index=sealed_generation_count,
                    stage_rows=stage_rows,
                )
                if not sealed:
                    raise RuntimeError(
                        f"Expected a non-empty generation at delta {delta_index}"
                    )
                total_seal_ms += seal_ms
                total_generation_plaintext_bytes += sum(
                    len(deltas[i])
                    for i in range(
                        delta_index - min(args.batch_size, delta_index),
                        delta_index,
                    )
                )
                sealed_generation_count += 1

            logical_version = delta_index
            version_rows.append({
                "scenario": "incremental",
                "run_index": run_index,
                "logical_version": logical_version,
                "store_version": int(record["version"]),
                "delta_size_bytes": len(delta),
                "append_size_bytes": len(delta),
                "snapshot_size_bytes": len(
                    expected_snapshots[logical_version]
                ),
                "append_ms": append_ms,
                "seal_ms": seal_ms,
                "write_total_ms": append_ms + seal_ms,
                "append_status": record.get("status"),
                "generation_sealed": is_batch_end,
                "data_shards": args.data_shards,
                "total_shards": args.total_shards,
                "batch_size": args.batch_size,
            })

        # Read generation metadata after write timings have completed.
        # _load() is an internal metadata accessor; it is used here only
        # for benchmark reporting because no public generation-list API
        # currently exists.
        state = store._load(object_id)
        generations = state.get("generations", [])

        for generation_index, generation in enumerate(generations):
            generation_id = generation["generation_id"]
            storage_metadata = store.storage.get_chunk_metadata(
                generation_id
            )

            generation_rows.append({
                "scenario": "incremental",
                "run_index": run_index,
                "generation_index": generation_index,
                "generation_id": generation_id,
                "start_store_version": generation["start_version"],
                "end_store_version": generation["end_version"],
                "start_logical_version": int(
                    generation["start_version"]
                ) - 1,
                "end_logical_version": int(
                    generation["end_version"]
                ) - 1,
                "generation_plaintext_bytes": generation[
                    "plaintext_size_bytes"
                ],
                "generation_ciphertext_bytes": storage_metadata.original_size,
                "chunk_root": generation["chunk_root"],
                "status": generation["status"],
                "data_shards": generation["data_shards"],
                "total_shards": generation["total_shards"],
            })

        # Retrieve every version and compare with its expected snapshot.
        retrieval_rows: list[dict[str, Any]] = []
        for logical_version, expected in enumerate(expected_snapshots):
            store_version = logical_version + 1
            restored, retrieval_ms = measure_ms(
                store.get_version,
                object_id,
                store_version,
            )
            total_retrieval_ms += retrieval_ms

            success = restored == expected
            if not success:
                raise RuntimeError(
                    f"Retrieval mismatch at logical version {logical_version}"
                )

            retrieval_rows.append({
                "scenario": "incremental",
                "run_index": run_index,
                "logical_version": logical_version,
                "store_version": store_version,
                "snapshot_size_bytes": len(expected),
                "retrieval_ms": retrieval_ms,
                "retrieval_success": success,
            })

        # Verify the complete version/generation history.
        history_valid, verify_history_ms = measure_ms(
            store.verify_history, object_id
        )
        if not history_valid:
            raise RuntimeError("Incremental history verification failed")

        # Attach retrieval measurements to the corresponding version rows.
        retrieval_by_version = {
            row["logical_version"]: row for row in retrieval_rows
        }
        for row in version_rows:
            retrieval = retrieval_by_version[row["logical_version"]]
            row["retrieval_ms"] = retrieval["retrieval_ms"]
            row["retrieval_success"] = retrieval["retrieval_success"]

        stored_shard_bytes = store.storage.get_stored_bytes()
        total_delta_bytes = sum(map(len, deltas))
        total_logical_bytes = len(initial_data) + total_delta_bytes

        # Sum of plaintext bytes actually encrypted and RS-encoded when
        # generations were sealed. This is the key incremental-work metric.
        processing_amplification = (
            total_generation_plaintext_bytes / total_logical_bytes
            if total_logical_bytes
            else None
        )

        challenge_row = benchmark_challenge(
            store=store,
            object_id=object_id,
            k=3,
        )

        challenge_row.update({
            "scenario": "incremental",
            "run_index": run_index,
            "data_shards": args.data_shards,
            "total_shards": args.total_shards,
            "batch_size": args.batch_size,
        })

        sequence_wall_ms = (
            time.perf_counter() - sequence_start
        ) * 1000.0

        sequence_row = {
            "scenario": "incremental",
            "run_index": run_index,
            "versions_processed": len(expected_snapshots),
            "generation_count": len(generations),
            "batch_size": args.batch_size,
            "initial_size_bytes": len(initial_data),
            "total_delta_bytes": total_delta_bytes,
            "final_size_bytes": len(expected_snapshots[-1]),
            "total_logical_bytes": total_logical_bytes,
            "total_generation_plaintext_bytes": (
                total_generation_plaintext_bytes
            ),
            "total_generation_ciphertext_bytes": sum(
                row["generation_ciphertext_bytes"]
                for row in generation_rows
            ),
            "stored_shard_bytes": stored_shard_bytes,
            "processing_amplification": processing_amplification,
            "append_total_ms": total_append_ms,
            "seal_total_ms": total_seal_ms,
            "write_total_ms": total_append_ms + total_seal_ms,
            "retrieval_total_ms": total_retrieval_ms,
            "verify_history_ms": verify_history_ms,
            "sequence_wall_ms_including_harness": sequence_wall_ms,
            "all_retrievals_success": True,
            "history_verification_success": history_valid,
            "data_shards": args.data_shards,
            "total_shards": args.total_shards,
        }

    return version_rows, generation_rows, stage_rows, sequence_row, challenge_row


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark incremental storage using append-only deltas "
            "and generation batching."
        )
    )
    parser.add_argument("--initial-size-mib", type=float, default=1.0)
    parser.add_argument("--delta-size-mib", type=float, default=0.1)
    parser.add_argument("--updates", type=int, default=5)
    parser.add_argument(
        "--batch-size",
        type=int,
        default=2,
        help="Number of appended deltas grouped into one generation.",
    )
    parser.add_argument("--repeat", type=int, default=10)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--data-shards", type=int, default=4)
    parser.add_argument("--total-shards", type=int, default=8)
    parser.add_argument("--chunk-size", type=int, default=4096)
    parser.add_argument("--seed", type=int, default=20261029)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "results" / "incremental",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    _validate_args(args)

    initial_size_bytes = int(args.initial_size_mib * MIB)
    delta_size_bytes = int(args.delta_size_mib * MIB)
    if initial_size_bytes <= 0 or delta_size_bytes <= 0:
        raise ValueError(
            "Selected MiB sizes are too small to produce non-empty payloads"
        )

    # Generate deterministic workload once, outside all measured sequences.
    workload = generate_update_workload(
        initial_size=initial_size_bytes,
        delta_size=delta_size_bytes,
        num_deltas=args.updates,
        seed=args.seed,
    )

    version_rows: list[dict[str, Any]] = []
    generation_rows: list[dict[str, Any]] = []
    sequence_rows: list[dict[str, Any]] = []
    stage_rows: list[dict[str, Any]] = []
    challenge_rows: list[dict[str, Any]] = []

    # Warmup sequences use independent temporary stores and are excluded.
    for warmup_index in range(args.warmup):
        run_sequence(
            workload.initial_data,
            workload.deltas,
            run_index=-(warmup_index + 1),
            args=args,
        )

    for run_index in range(args.repeat):
        versions, generations, stages, sequence, challenge = run_sequence(
            workload.initial_data,
            workload.deltas,
            run_index=run_index,
            args=args,
        )
        version_rows.extend(versions)
        generation_rows.extend(generations)
        stage_rows.extend(stages)
        sequence_rows.append(sequence)
        challenge_rows.append(challenge)

    version_summary = _summarize_rows(
        version_rows, "logical_version"
    )
    sequence_numeric = [
        {
            key: value
            for key, value in row.items()
            if isinstance(value, (int, float))
            and not isinstance(value, bool)
            and key not in {
                "run_index",
                "versions_processed",
                "generation_count",
                "batch_size",
                "initial_size_bytes",
                "total_delta_bytes",
                "final_size_bytes",
                "total_logical_bytes",
                "data_shards",
                "total_shards",
            }
        }
        for row in sequence_rows
    ]

    summary = {
        "benchmark": "B1_incremental_storage",
        "description": (
            "Append deltas into generations; seal each generation once."
        ),
        "config": {
            "initial_size_bytes": initial_size_bytes,
            "delta_size_bytes": delta_size_bytes,
            "updates": args.updates,
            "batch_size": args.batch_size,
            "repeat": args.repeat,
            "warmup": args.warmup,
            "data_shards": args.data_shards,
            "total_shards": args.total_shards,
            "chunk_size": args.chunk_size,
            "seed": args.seed,
        },
        "workload": {
            "initial_size_bytes": workload.initial_size_bytes,
            "delta_sizes_bytes": list(workload.delta_sizes_bytes),
            "final_size_bytes": workload.final_size_bytes,
        },
        "environment": {
            "python_version": sys.version,
            "platform": platform.platform(),
        },
        "sequence_summary": numeric_summary(sequence_numeric),
        "version_summary": version_summary,
        "generation_summary": {
            "runs": len(generation_rows),
            "generation_count_per_run": (
                len(generation_rows) // args.repeat
                if args.repeat
                else 0
            ),
            "total_generation_plaintext_bytes_per_run": (
                statistics.mean(
                    row["total_generation_plaintext_bytes"]
                    for row in sequence_rows
                )
                if sequence_rows
                else 0
            ),
        },
        "stage_summary": {
            stage: numeric_summary([
                {f"{stage}_ms": row[f"{stage}_ms"]}
                for row in stage_rows
            ])
            for stage in STAGE_NAMES
        },
        "success": {
            "all_retrievals": all(
                row["all_retrievals_success"]
                for row in sequence_rows
            ),
            "all_history_verifications": all(
                row["history_verification_success"]
                for row in sequence_rows
            ),
        },
    }

    version_export = export_results(
        output_dir=args.output_dir,
        prefix="incremental_versions",
        rows=version_rows,
        summary={
            "benchmark": "B1_incremental_storage_versions",
            "config": summary["config"],
            "version_summary": version_summary,
        },
    )

    generation_export = None
    if generation_rows:
        generation_export = export_results(
            output_dir=args.output_dir,
            prefix="incremental_generations",
            rows=generation_rows,
            summary={
                "benchmark": "B1_incremental_storage_generations",
                "config": summary["config"],
            },
        )

    stage_export = None
    if stage_rows:
        stage_export = export_results(
            output_dir=args.output_dir,
            prefix="incremental_stages",
            rows=stage_rows,
            summary={
                "benchmark": "B1_incremental_storage_stages",
                "config": summary["config"],
                "stage_summary": summary["stage_summary"],
            },
        )

    challenge_export = None
    if challenge_rows:
        challenge_export = export_results(
            output_dir=args.output_dir,
            prefix="incremental_challenges",
            rows=challenge_rows,
            summary={
                "benchmark": "B1_incremental_storage_challenges",
                "config": summary["config"],
            },
        )

    sequence_export = export_results(
        output_dir=args.output_dir,
        prefix="incremental_sequences",
        rows=sequence_rows,
        summary=summary,
    )

    print("Incremental-storage benchmark completed.")
    print(f"Version CSV:   {version_export.csv_path}")
    print(f"Version JSON:  {version_export.json_path}")
    if generation_export is not None:
        print(f"Generation CSV:  {generation_export.csv_path}")
        print(f"Generation JSON: {generation_export.json_path}")
    if stage_export is not None:
        print(f"Stage CSV:  {stage_export.csv_path}")
        print(f"Stage JSON: {stage_export.json_path}")
    if challenge_export is not None:
        print(f"Challenge CSV:  {challenge_export.csv_path}")
        print(f"Challenge JSON: {challenge_export.json_path}")
    print(f"Sequence CSV:  {sequence_export.csv_path}")
    print(f"Sequence JSON: {sequence_export.json_path}")


if __name__ == "__main__":
    main()
