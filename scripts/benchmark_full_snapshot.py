#!/usr/bin/env python3
"""Benchmark full-snapshot updates using the existing Engram one-object pipeline.

Each update appends a deterministic delta to the logical object. The benchmark
re-runs the existing full pipeline over the *entire current snapshot* at every
version, which is the B0 full-snapshot baseline.

Expected project layout:
  engram-simulation/
    src/engram/experiment.py   # exposes one_run(...)
    scripts/benchmark_full_snapshot.py

If experiment.py lives elsewhere, adjust the import block below.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if SRC_DIR.exists():
    sys.path.insert(0, str(SRC_DIR))

# Reuse the same implementation as the current one-object benchmark so that
# encryption, Reed-Solomon, storage, and proof timings remain comparable.
try:
    from engram.experiment import one_run
except ImportError as exc:
    raise ImportError(
        "Cannot import one_run from engram.experiment. "
        "If your experiment.py is in another location, update this import."
    ) from exc

MIB = 1024 * 1024


def deterministic_bytes(size: int, seed: int) -> bytes:
    """Create reproducible pseudo-random-looking bytes without external files."""
    # Repeating a SHA-256 stream makes workloads reproducible and avoids
    # allocating a large random string through repeated random calls.
    import hashlib

    out = bytearray()
    counter = 0
    seed_bytes = str(seed).encode("ascii")
    while len(out) < size:
        out.extend(hashlib.sha256(seed_bytes + counter.to_bytes(8, "big")).digest())
        counter += 1
    return bytes(out[:size])


def build_snapshots(
    initial_size: int,
    delta_size: int,
    updates: int,
    seed: int,
) -> list[tuple[int, int, bytes]]:
    """Return (version, delta_size, full_snapshot_payload) for v0..vN."""
    payload = deterministic_bytes(initial_size, seed)
    snapshots = [(0, 0, payload)]
    for version in range(1, updates + 1):
        delta = deterministic_bytes(delta_size, seed + version)
        payload = payload + delta
        snapshots.append((version, delta_size, payload))
    return snapshots


def flatten_dict(obj: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten nested dictionaries so metric fields can be written to CSV."""
    result: dict[str, Any] = {}
    if isinstance(obj, dict):
        for key, value in obj.items():
            name = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, dict):
                result.update(flatten_dict(value, name))
            elif isinstance(value, (str, int, float, bool)) or value is None:
                result[name] = value
            else:
                result[name] = json.dumps(value, ensure_ascii=False, default=str)
    else:
        result[prefix or "result"] = obj
    return result


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * p
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (rank - lo)


def numeric_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    keys = sorted({k for row in rows for k, v in row.items()
                   if isinstance(v, (int, float)) and not isinstance(v, bool)})
    summary: dict[str, Any] = {}
    for key in keys:
        vals = [float(row[key]) for row in rows
                if isinstance(row.get(key), (int, float))
                and not isinstance(row.get(key), bool)]
        if not vals:
            continue
        summary[key] = {
            "n": len(vals),
            "median": statistics.median(vals),
            "p95": percentile(vals, 0.95),
            "min": min(vals),
            "max": max(vals),
        }
    return summary


def run_sequence(
    snapshots: list[tuple[int, int, bytes]],
    run_index: int,
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    version_rows: list[dict[str, Any]] = []
    sequence_start = time.perf_counter()
    total_plaintext_processed = 0
    total_delta_bytes = 0
    total_ciphertext_bytes = 0
    total_shard_bytes_written = 0

    # one_run currently creates a fresh temporary StorageManager per snapshot.
    # For this B0 benchmark, versions are logically retained: cumulative
    # retained bytes are the sum of each independently measured snapshot's
    # shard bytes. This avoids changing the current one-object benchmark code.
    for version, delta_bytes, payload in snapshots:
        version_start = time.perf_counter()
        file_path = PROJECT_ROOT / "data" / "benchmark_workloads" / (
            f"full_snapshot_run{run_index}_v{version}.bin"
        )
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(payload)
        try:
            result = one_run(
                file_path=str(file_path),
                payload=payload,
                repeat=run_index,
                run_type="full_pipeline",
                data_shards=args.data_shards,
                total_shards=args.total_shards,
                challenge_size=args.challenge_size,
            )
        finally:
            file_path.unlink(missing_ok=True)

        metrics = flatten_dict(result)
        version_elapsed_ms = (time.perf_counter() - version_start) * 1000.0
        row: dict[str, Any] = {
            "run_index": run_index,
            "version": version,
            "snapshot_size_bytes": len(payload),
            "delta_size_bytes": delta_bytes,
            "full_snapshot_reprocessed_bytes": len(payload),
            "version_wall_ms_including_harness": version_elapsed_ms,
        }
        row.update(metrics)
        version_rows.append(row)

        total_plaintext_processed += len(payload)
        total_delta_bytes += delta_bytes

        # Map common names from the existing experiment result into aggregate
        # sequence metrics. Other original metrics remain in the per-version CSV.
        for key, value in metrics.items():
            normalized = key.lower()
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if "ciphertext" in normalized and "size" in normalized:
                    total_ciphertext_bytes += int(value)
                if ("stored_bytes" in normalized or "stored_payload_bytes" in normalized
                        or "storage_bytes" in normalized):
                    total_shard_bytes_written += int(value)

    sequence_wall_ms = (time.perf_counter() - sequence_start) * 1000.0
    final_size = len(snapshots[-1][2]) if snapshots else 0
    sequence_row = {
        "run_index": run_index,
        "versions_processed": len(snapshots),
        "initial_size_bytes": len(snapshots[0][2]) if snapshots else 0,
        "final_size_bytes": final_size,
        "total_delta_bytes": total_delta_bytes,
        "total_plaintext_processed_bytes": total_plaintext_processed,
        "processing_amplification": (
            total_plaintext_processed / final_size if final_size else None
        ),
        "total_ciphertext_bytes_observed": total_ciphertext_bytes,
        "total_shard_bytes_written_observed": total_shard_bytes_written,
        "sequence_wall_ms_including_harness": sequence_wall_ms,
    }
    return version_rows, sequence_row


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    fieldnames = sorted({key for row in rows for key in row})
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark B0 full-snapshot updates using the current pipeline."
    )
    parser.add_argument("--initial-size-mib", type=float, default=10.0)
    parser.add_argument("--delta-size-mib", type=float, default=1.0)
    parser.add_argument("--updates", type=int, default=5,
                        help="Number of updates after version 0.")
    parser.add_argument("--repeat", type=int, default=10,
                        help="Number of measured independent sequences.")
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--data-shards", type=int, default=4)
    parser.add_argument("--total-shards", type=int, default=8)
    parser.add_argument("--challenge-size", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20261029)
    parser.add_argument("--output-dir", type=Path,
                        default=PROJECT_ROOT / "results" / "full_snapshot")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.initial_size_mib <= 0 or args.delta_size_mib <= 0:
        raise SystemExit("Initial size and delta size must be positive.")
    if args.updates < 0 or args.repeat <= 0 or args.warmup < 0:
        raise SystemExit("updates/warmup must be >= 0 and repeat must be > 0.")
    if args.data_shards <= 0 or args.total_shards < args.data_shards:
        raise SystemExit("Require total-shards >= data-shards > 0.")

    initial_size = int(args.initial_size_mib * MIB)
    delta_size = int(args.delta_size_mib * MIB)
    snapshots = build_snapshots(initial_size, delta_size, args.updates, args.seed)

    print("Full-snapshot benchmark configuration")
    print(f"  Versions: {len(snapshots)} (v0..v{args.updates})")
    print(f"  Initial size: {initial_size:,} bytes")
    print(f"  Delta size: {delta_size:,} bytes")
    print(f"  Final size: {len(snapshots[-1][2]):,} bytes")
    print(f"  RS: {args.data_shards}+{args.total_shards - args.data_shards}")
    print(f"  Warmup sequences: {args.warmup}; measured sequences: {args.repeat}")

    # Warm-ups are executed but excluded from exported raw samples.
    for warmup_index in range(args.warmup):
        print(f"[warmup {warmup_index + 1}/{args.warmup}]")
        run_sequence(snapshots, -(warmup_index + 1), args)

    all_version_rows: list[dict[str, Any]] = []
    sequence_rows: list[dict[str, Any]] = []
    for run_index in range(args.repeat):
        print(f"[run {run_index + 1}/{args.repeat}]")
        version_rows, sequence_row = run_sequence(snapshots, run_index, args)
        all_version_rows.extend(version_rows)
        sequence_rows.append(sequence_row)

    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / "full_snapshot_versions.csv", all_version_rows)
    write_csv(out / "full_snapshot_sequences.csv", sequence_rows)

    summary = {
        "config": {
            "initial_size_bytes": initial_size,
            "delta_size_bytes": delta_size,
            "updates": args.updates,
            "versions_per_sequence": len(snapshots),
            "repeat": args.repeat,
            "warmup": args.warmup,
            "data_shards": args.data_shards,
            "total_shards": args.total_shards,
            "challenge_size": args.challenge_size,
            "seed": args.seed,
            "baseline": "B0_full_snapshot",
        },
        "sequence_metrics": numeric_summary(sequence_rows),
        "per_version_metrics": {},
        "files": [
            "full_snapshot_versions.csv",
            "full_snapshot_sequences.csv",
        ],
    }
    for version in range(len(snapshots)):
        version_subset = [row for row in all_version_rows if row["version"] == version]
        summary["per_version_metrics"][str(version)] = numeric_summary(version_subset)

    (out / "full_snapshot_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print(f"\nResults written to: {out}")
    print(f"  - {out / 'full_snapshot_versions.csv'}")
    print(f"  - {out / 'full_snapshot_sequences.csv'}")
    print(f"  - {out / 'full_snapshot_summary.json'}")


if __name__ == "__main__":
    main()
