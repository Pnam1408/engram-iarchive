#benchmarks/scenarios/full_snapshot.py
"""B0 full-snapshot benchmark for the ENGRAM-iARCHIVE simulation.

For each version, the complete logical snapshot is sent through the existing
baseline pipeline from scratch. Each call to ``one_run`` creates its own
StorageManager, so storage state is isolated between versions. Python Merkle
proof generation/verification remain part of the pipeline; Groth16 is measured
separately by the dedicated zk benchmark.

Run from the repository root:
    python -m benchmarks.scenarios.full_snapshot \
        --initial-size-mib 1 --delta-size-mib 0.25 --updates 2 \
        --repeat 3 --warmup 1
"""
from __future__ import annotations

import argparse
import math
import platform
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

# Support ``python -m benchmarks.scenarios.full_snapshot`` from the project root.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from benchmarks.common.results import export_results
from benchmarks.common.statistics import numeric_summary
from benchmarks.common.workload import build_snapshots
from benchmarks.scenarios.baseline_pipeline import one_run

MIB = 1024 * 1024

# Fields that describe the workload/configuration rather than measured outcomes.
_NON_METRIC_NUMERIC_FIELDS = {
    "run_index",
    "repeat",
    "version",
    "snapshot_size_bytes",
    "delta_size_bytes",
    "full_snapshot_reprocessed_bytes",
    "input_size_bytes",
    "data_shards",
    "total_shards",
    "challenge_size",
    "available_shards",
    "chunks_per_shard",

    # Sequence workload descriptors, not performance measurements.
    "versions_processed",
    "initial_size_bytes",
    "final_size_bytes",
    "total_delta_bytes",
    "total_logical_bytes_added",
}


def _validate_args(args: argparse.Namespace) -> None:
    for name in ("initial_size_mib", "delta_size_mib"):
        value = getattr(args, name)
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"--{name.replace('_', '-')} must be a finite number > 0")
    if args.updates < 0:
        raise ValueError("--updates must be >= 0")
    if args.repeat < 1:
        raise ValueError("--repeat must be >= 1")
    if args.warmup < 0:
        raise ValueError("--warmup must be >= 0")
    if args.data_shards < 1:
        raise ValueError("--data-shards must be >= 1")
    if args.total_shards < args.data_shards:
        raise ValueError("--total-shards must be >= --data-shards")
    if not 1 <= args.challenge_size <= args.total_shards:
        raise ValueError("--challenge-size must be between 1 and --total-shards")


def _metric_values(row: Mapping[str, Any]) -> dict[str, Any]:
    """Keep numeric outcomes while excluding IDs, dimensions, and config."""
    return {
        key: value
        for key, value in row.items()
        if key not in _NON_METRIC_NUMERIC_FIELDS
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    }


def summarize_versions(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Summarize measured runs separately for each snapshot version."""
    versions = sorted({int(row["version"]) for row in rows})
    summaries: list[dict[str, Any]] = []
    for version in versions:
        group = [row for row in rows if int(row["version"]) == version]
        summary: dict[str, Any] = {
            "version": version,
            "runs": len(group),
            "snapshot_size_bytes": group[0]["snapshot_size_bytes"],
            "delta_size_bytes": group[0]["delta_size_bytes"],
            "full_snapshot_reprocessed_bytes": group[0]["full_snapshot_reprocessed_bytes"],
        }
        summary.update(numeric_summary([_metric_values(row) for row in group]))
        summaries.append(summary)
    return summaries


def _write_snapshot_files(
    snapshots: Sequence[tuple[int, int, bytes]],
    directory: Path,
) -> dict[int, Path]:
    """Materialize deterministic inputs before timing the benchmark sequence."""
    paths: dict[int, Path] = {}
    for version, _delta_size, payload in snapshots:
        path = directory / f"snapshot_v{version}.bin"
        path.write_bytes(payload)
        paths[version] = path
    return paths


def run_sequence(
    snapshots: Sequence[tuple[int, int, bytes]],
    file_paths: Mapping[int, Path],
    run_index: int,
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run one complete B0 sequence and return per-version and sequence rows."""
    version_rows: list[dict[str, Any]] = []
    sequence_start = time.perf_counter()
    total_snapshot_bytes_processed = 0
    total_delta_bytes = 0
    total_stored_shard_bytes = 0
    total_pipeline_ms = 0.0

    for version, delta_size_bytes, payload in snapshots:
        # one_run() owns its temporary StorageManager, so each version starts
        # from an empty storage state and stores the entire snapshot anew.
        result = one_run(
            file_path=file_paths[version],
            payload=payload,
            repeat=run_index,
            run_type="full_snapshot",
            data_shards=args.data_shards,
            total_shards=args.total_shards,
            challenge_size=args.challenge_size,
        )

        row: dict[str, Any] = dict(result)
        pipeline_scenario = row.get("scenario")
        # Preserve the baseline pipeline's original metric names and values,
        # while making the outer experiment's scenario/version explicit.
        row.update({
            "scenario": "full_snapshot",
            "pipeline_scenario": pipeline_scenario,
            "run_index": run_index,
            "version": version,
            "snapshot_size_bytes": len(payload),
            "delta_size_bytes": delta_size_bytes,
            "full_snapshot_reprocessed_bytes": len(payload),
        })
        version_rows.append(row)

        total_snapshot_bytes_processed += len(payload)
        total_delta_bytes += delta_size_bytes
        stored_bytes = result.get("stored_payload_bytes")
        if isinstance(stored_bytes, (int, float)) and not isinstance(stored_bytes, bool):
            total_stored_shard_bytes += int(stored_bytes)
        pipeline_ms = result.get("pipeline_end_to_end_ms")
        if isinstance(pipeline_ms, (int, float)) and not isinstance(pipeline_ms, bool):
            total_pipeline_ms += float(pipeline_ms)

    sequence_wall_ms = (time.perf_counter() - sequence_start) * 1000.0
    initial_size = len(snapshots[0][2]) if snapshots else 0
    final_size = len(snapshots[-1][2]) if snapshots else 0
    # For an append-only workload, initial bytes + all deltas == final snapshot.
    total_logical_bytes = initial_size + total_delta_bytes
    sequence_row: dict[str, Any] = {
        "scenario": "full_snapshot",
        "run_index": run_index,
        "versions_processed": len(snapshots),
        "initial_size_bytes": initial_size,
        "final_size_bytes": final_size,
        "total_delta_bytes": total_delta_bytes,
        "total_logical_bytes_added": total_logical_bytes,
        "total_snapshot_bytes_processed": total_snapshot_bytes_processed,
        "processing_amplification": (
            total_snapshot_bytes_processed / total_logical_bytes
            if total_logical_bytes
            else None
        ),
        # Sum of bytes written by each isolated per-version storage instance.
        # This is cumulative write volume, not simultaneous retained disk usage.
        "total_stored_shard_bytes_across_versions": total_stored_shard_bytes,
        "sum_pipeline_end_to_end_ms": total_pipeline_ms,
        "sequence_wall_ms_including_harness": sequence_wall_ms,
    }
    return version_rows, sequence_row


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark B0: reprocess every complete snapshot from scratch. "
            "Groth16 proving is benchmarked separately."
        )
    )
    parser.add_argument("--initial-size-mib", type=float, default=1.0)
    parser.add_argument("--delta-size-mib", type=float, default=0.25)
    parser.add_argument("--updates", type=int, default=5,
                        help="Number of appended deltas after version 0.")
    parser.add_argument("--repeat", type=int, default=10,
                        help="Number of measured independent sequences.")
    parser.add_argument("--warmup", type=int, default=2,
                        help="Number of whole sequences to run and exclude from results.")
    parser.add_argument("--data-shards", type=int, default=4)
    parser.add_argument("--total-shards", type=int, default=8)
    parser.add_argument("--challenge-size", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20261029)
    parser.add_argument("--output-dir", type=Path,
                        default=PROJECT_ROOT / "results" / "full_snapshot")
    return parser.parse_args(argv)


def _export_path(exported: Any, kind: str) -> Any:
    """Read an export path from either common ExportedResults naming style."""
    return getattr(exported, f"{kind}_path", getattr(exported, kind, exported))


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    _validate_args(args)

    initial_size_bytes = int(args.initial_size_mib * MIB)
    delta_size_bytes = int(args.delta_size_mib * MIB)
    if initial_size_bytes <= 0 or delta_size_bytes <= 0:
        raise ValueError("The selected MiB sizes are too small to produce non-empty byte payloads")

    snapshots = build_snapshots(
        initial_size=initial_size_bytes,
        delta_size=delta_size_bytes,
        updates=args.updates,
        seed=args.seed,
    )

    version_rows: list[dict[str, Any]] = []
    sequence_rows: list[dict[str, Any]] = []

    # Build each input file once, before warmups and measured sequences. Workload
    # generation and file materialization therefore do not contaminate pipeline
    # stage timings. Storage itself is recreated inside one_run for each version.
    with tempfile.TemporaryDirectory(prefix="engram_full_snapshot_inputs_") as temp_dir:
        file_paths = _write_snapshot_files(snapshots, Path(temp_dir))

        for warmup_index in range(args.warmup):
            run_sequence(snapshots, file_paths, run_index=-(warmup_index + 1), args=args)

        for run_index in range(args.repeat):
            rows, sequence_row = run_sequence(
                snapshots=snapshots,
                file_paths=file_paths,
                run_index=run_index,
                args=args,
            )
            version_rows.extend(rows)
            sequence_rows.append(sequence_row)

    # Fail early if the pipeline reported a failed reconstruction or proof.
    failed_reconstruction = [
        row for row in version_rows if row.get("reconstruction_success") is not True
    ]
    failed_proofs = [
        row
        for row in version_rows
        if (
            row.get("python_merkle_proof_success") is not True
            or row.get("challenge_target_count") != args.challenge_size
            or row.get("proof_count") != args.challenge_size
            or row.get("verified_proof_count") != args.challenge_size
            or row.get("failed_proof_count") != 0
        )
    ]

    if failed_reconstruction or failed_proofs:
        raise RuntimeError(
            "Full-snapshot benchmark correctness check failed: "
            f"reconstruction failures={len(failed_reconstruction)}, "
            f"Python Merkle proof failures={len(failed_proofs)}"
        )

    config = {
        "initial_size_mib": args.initial_size_mib,
        "delta_size_mib": args.delta_size_mib,
        "updates": args.updates,
        "repeat": args.repeat,
        "warmup": args.warmup,
        "data_shards": args.data_shards,
        "total_shards": args.total_shards,
        "challenge_size": args.challenge_size,
        "seed": args.seed,
        "storage_isolation": "fresh StorageManager for every snapshot version",
        "proof_scope": (
            "One Python two-level Merkle proof per challenge target; "
            "all target proofs are verified by the Python Validator. "
            "This is not Groth16 proving or cryptographic proof aggregation."
        ),
        "workload_generation_and_input_file_writes_timed": False,
    }
    environment = {
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
    }
    version_summary = {
        "scenario": "full_snapshot",
        "config": config,
        "environment": environment,
        "semantics": {
            "B0": "Each version reprocesses the complete current snapshot from scratch.",
            "storage": "Each one_run creates a new temporary storage root; versions do not share state.",
            "processing_amplification": (
                "sum(snapshot sizes across versions) / final logical object size"
            ),
            "storage_byte_metric": (
                "Sum of stored shard bytes reported by each isolated version; "
                "not simultaneous retained storage capacity."
            ),
            "proof": (
                "Each challenge target identifies one commitment, shard, and chunk. "
                "The pipeline generates one Python Merkle proof per target."
            ),
        },
        "versions": summarize_versions(version_rows),
        "sequence_runs": numeric_summary([
            _metric_values(row) for row in sequence_rows
        ]),
        "all_reconstructions_passed": all(
            row.get("reconstruction_success") is True for row in version_rows
        ),
        "all_python_merkle_proofs_passed": all(
            row.get("python_merkle_proof_success") is True for row in version_rows
        ),
    }
    sequence_summary = {
        "scenario": "full_snapshot",
        "config": config,
        "environment": environment,
        "summary": numeric_summary([_metric_values(row) for row in sequence_rows]),
        "all_reconstructions_passed": version_summary["all_reconstructions_passed"],
        "all_python_merkle_proofs_passed": version_summary["all_python_merkle_proofs_passed"],
    }

    version_export = export_results(
        output_dir=args.output_dir,
        prefix="full_snapshot_versions",
        rows=version_rows,
        summary=version_summary,
        csv_fieldnames=list(dict.fromkeys(
            key for row in version_rows for key in row.keys()
        )),
    )
    sequence_export = export_results(
        output_dir=args.output_dir,
        prefix="full_snapshot_sequences",
        rows=sequence_rows,
        summary=sequence_summary,
        csv_fieldnames=list(dict.fromkeys(
            key for row in sequence_rows for key in row.keys()
        )),
    )

    print(f"Measured sequences: {args.repeat}; warmup sequences excluded: {args.warmup}")
    print(f"Snapshot versions per sequence: {len(snapshots)}")
    print(f"All reconstructions passed: {version_summary['all_reconstructions_passed']}")
    print(f"All Python Merkle proofs passed: {version_summary['all_python_merkle_proofs_passed']}")
    print(f"Version CSV: {_export_path(version_export, 'csv')}")
    print(f"Version JSON: {_export_path(version_export, 'json')}")
    print(f"Sequence CSV: {_export_path(sequence_export, 'csv')}")
    print(f"Sequence JSON: {_export_path(sequence_export, 'json')}")


if __name__ == "__main__":
    main()
