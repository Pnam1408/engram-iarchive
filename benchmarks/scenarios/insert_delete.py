"""Deterministic Insert/Delete benchmark for Fixed-size vs FastCDC.

Run from the repository root, after building the FastCDC native extension:

    PYTHONPATH=src python -m benchmarks.scenarios.insert_delete \\
        --initial-size-mib 1 --insert-size-kib 16 --delete-size-kib 16 \\
        --operations 12 --batch-size 2 --repeat 3 --warmup 1

The benchmark times operation recording and generation sealing separately,
then validates every version through both operation replay and chunk manifests.
It measures logical chunk reuse; it does not claim distributed shard savings.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from benchmarks.common.results import export_results
from benchmarks.common.statistics import numeric_summary
from benchmarks.common.timing import measure_ms
from benchmarks.common.workload import (
    InsertDeleteWorkload,
    generate_insert_delete_workload,
)
from engram.chunking import FastCDCChunker, FastCDCConfig, FixedSizeChunker
from engram.incremental.store import IncrementalStore

MIB = 1024 * 1024
KIB = 1024


def _parse_chunkers(value: str) -> list[str]:
    names = [item.strip().lower() for item in value.split(",") if item.strip()]
    if not names or any(item not in {"fixed", "fastcdc"} for item in names):
        raise ValueError("--chunkers accepts a comma-separated subset of fixed,fastcdc")
    if len(set(names)) != len(names):
        raise ValueError("--chunkers must not contain duplicates")
    return names


def _make_chunker(name: str, args: argparse.Namespace):
    if name == "fixed":
        return FixedSizeChunker(args.fixed_chunk_size)
    return FastCDCChunker(
        FastCDCConfig(
            min_size=args.fastcdc_min_size,
            avg_size=args.fastcdc_avg_size,
            max_size=args.fastcdc_max_size,
        )
    )


def _validate_args(args: argparse.Namespace) -> None:
    for name in ("initial_size_mib", "insert_size_kib", "delete_size_kib"):
        value = getattr(args, name)
        if value <= 0:
            raise ValueError(f"--{name.replace('_', '-')} must be > 0")
    if args.operations < 1:
        raise ValueError("--operations must be >= 1")
    if args.batch_size < 1:
        raise ValueError("--batch-size must be >= 1")
    if args.repeat < 1 or args.warmup < 0:
        raise ValueError("--repeat must be >= 1 and --warmup must be >= 0")
    if not (1 <= args.data_shards < args.total_shards <= 255):
        raise ValueError("Require 1 <= data-shards < total-shards <= 255")
    if args.fixed_chunk_size <= 0:
        raise ValueError("--fixed-chunk-size must be > 0")
    if not (
        64 <= args.fastcdc_min_size <= args.fastcdc_avg_size <= args.fastcdc_max_size
    ):
        raise ValueError("Require 64 <= fastcdc-min <= fastcdc-avg <= fastcdc-max")
    if args.fastcdc_avg_size & (args.fastcdc_avg_size - 1):
        raise ValueError("--fastcdc-avg-size must be a power of two")
    _parse_chunkers(args.chunkers)


def _run_one(
    workload: InsertDeleteWorkload,
    chunker_name: str,
    run_index: int,
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    object_id = f"insert-delete-{uuid.uuid4().hex[:12]}"
    operation_rows: list[dict[str, Any]] = []
    chunker = _make_chunker(chunker_name, args)

    # The benchmark explicitly seals by batch size. Keep automatic sealing out
    # of the measured trace while accounting for the initial snapshot payload.
    maximum_buffer = (
        len(workload.initial_data)
        + workload.total_inserted_bytes
        + len(workload.operations)
        + 1
    )
    with tempfile.TemporaryDirectory(prefix="engram_insert_delete_") as temp_dir:
        store = IncrementalStore(
            Path(temp_dir) / "store",
            data_shards=args.data_shards,
            total_shards=args.total_shards,
            chunk_size=args.fixed_chunk_size,
            size_threshold=maximum_buffer,
            max_wait_seconds=1e12,
            chunker=chunker,
        )

        sequence_start = time.perf_counter()
        _initial_record, initial_write_ms = measure_ms(
            store.append, object_id, workload.initial_data
        )
        initial_sealed, initial_seal_ms = measure_ms(store.seal, object_id)
        if not initial_sealed:
            raise RuntimeError("Initial snapshot generation was not sealed")
        initial_status = store.list_versions(object_id)[0].get("status")
        if initial_status != "Available":
            raise RuntimeError("Initial version did not become Available after sealing")

        seal_rows: list[dict[str, Any]] = [
            {
                "run_index": run_index,
                "chunker": chunker_name,
                "generation_index": 0,
                "seal_ms": initial_seal_ms,
            }
        ]
        total_operation_ms = 0.0
        total_seal_ms = initial_seal_ms
        generation_index = 0

        for operation in workload.operations:
            if operation.operation == "insert":
                record, elapsed_ms = measure_ms(
                    store.insert, object_id, operation.offset, operation.payload
                )
            else:
                record, elapsed_ms = measure_ms(
                    store.delete, object_id, operation.offset, operation.delete_length
                )
            total_operation_ms += elapsed_ms

            should_seal = (
                operation.index + 1
            ) % args.batch_size == 0 or operation.index + 1 == len(workload.operations)
            seal_ms = 0.0
            if should_seal:
                generation_index += 1
                sealed, seal_ms = measure_ms(store.seal, object_id)
                if not sealed:
                    raise RuntimeError(
                        f"Expected generation {generation_index} to seal"
                    )
                total_seal_ms += seal_ms
                seal_rows.append(
                    {
                        "run_index": run_index,
                        "chunker": chunker_name,
                        "generation_index": generation_index,
                        "seal_ms": seal_ms,
                    }
                )

            version = operation.index + 2  # initial append is store version 1
            if record.get("status") not in {"PENDING", "Available"}:
                raise RuntimeError(
                    f"Unexpected operation status: {record.get('status')}"
                )
            operation_rows.append(
                {
                    "run_index": run_index,
                    "chunker": chunker_name,
                    "operation_index": operation.index,
                    "operation": operation.operation,
                    "offset": operation.offset,
                    "insert_size_bytes": operation.payload_size_bytes,
                    "delete_length_bytes": operation.delete_length,
                    "result_size_bytes": len(workload.snapshots[operation.index + 1]),
                    "operation_ms": elapsed_ms,
                    "seal_after_operation": should_seal,
                    "seal_ms": seal_ms,
                    "store_version": version,
                    "status_before_batch_seal": record.get("status"),
                }
            )

        # Validate every immutable version via both independent retrieval paths.
        retrieval_total_ms = 0.0
        manifest_total_chunk_bytes = 0
        manifest_total_chunk_refs = 0
        unique_descriptors: dict[str, int] = {}
        version_count = len(workload.snapshots)
        for store_version, expected in enumerate(workload.snapshots, start=1):
            restored, retrieval_ms = measure_ms(
                store.get_version, object_id, store_version
            )
            chunk_restored, chunk_retrieval_ms = measure_ms(
                store.get_version_from_chunks, object_id, store_version
            )
            retrieval_total_ms += retrieval_ms + chunk_retrieval_ms
            if restored != expected or chunk_restored != expected:
                raise RuntimeError(
                    f"Version reconstruction mismatch at version {store_version}"
                )

            manifest = store.get_chunk_manifest(object_id, store_version)
            for descriptor in manifest["chunks"]:
                digest = str(descriptor["sha256"])
                size = int(descriptor["size"])
                unique_descriptors.setdefault(digest, size)
                manifest_total_chunk_bytes += size
                manifest_total_chunk_refs += 1

        history_valid, verify_history_ms = measure_ms(store.verify_history, object_id)
        if not history_valid:
            raise RuntimeError("verify_history returned False")

        latest_stats = store.get_chunk_storage_stats(object_id, version_count)
        unique_plaintext_chunk_bytes = sum(unique_descriptors.values())
        dedup_savings = (
            1.0 - unique_plaintext_chunk_bytes / manifest_total_chunk_bytes
            if manifest_total_chunk_bytes
            else 0.0
        )
        chunk_reuse_ratio = (
            1.0 - len(unique_descriptors) / manifest_total_chunk_refs
            if manifest_total_chunk_refs
            else 0.0
        )
        sequence_wall_ms = (time.perf_counter() - sequence_start) * 1000.0

        sequence_row = {
            "run_index": run_index,
            "chunker": chunker_name,
            "pattern": workload.pattern,
            "operations": len(workload.operations),
            "versions": version_count,
            "batch_size": args.batch_size,
            "initial_size_bytes": len(workload.initial_data),
            "final_size_bytes": workload.final_size_bytes,
            "inserted_bytes": workload.total_inserted_bytes,
            "deleted_bytes": workload.total_deleted_bytes,
            "initial_append_ms": initial_write_ms,
            "total_operation_ms": total_operation_ms,
            "mean_operation_ms": total_operation_ms / len(workload.operations),
            "total_seal_ms": total_seal_ms,
            "mean_seal_ms": total_seal_ms / len(seal_rows),
            "retrieval_total_ms": retrieval_total_ms,
            "verify_history_ms": verify_history_ms,
            "sequence_wall_ms": sequence_wall_ms,
            "manifest_total_chunk_bytes": manifest_total_chunk_bytes,
            "full_snapshot_chunking_input_bytes": sum(
                len(item) for item in workload.snapshots
            ),
            "unique_plaintext_chunk_bytes": unique_plaintext_chunk_bytes,
            "unique_encrypted_chunk_bytes": latest_stats[
                "unique_encrypted_chunk_bytes"
            ],
            "manifest_total_chunk_refs": manifest_total_chunk_refs,
            "unique_chunk_count": len(unique_descriptors),
            "chunk_reuse_ratio": chunk_reuse_ratio,
            "logical_dedup_savings_ratio": dedup_savings,
            "latest_chunk_count": latest_stats["chunk_count"],
            "latest_content_size_bytes": latest_stats["content_size_bytes"],
            "history_valid": history_valid,
            "all_versions_reconstructed": True,
            "data_shards": args.data_shards,
            "total_shards": args.total_shards,
            "chunker_algorithm": getattr(chunker, "algorithm", "fixed-size"),
        }

    return operation_rows, sequence_row


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark deterministic Insert/Delete workloads."
    )
    parser.add_argument("--initial-size-mib", type=float, default=1.0)
    parser.add_argument("--insert-size-kib", type=float, default=16.0)
    parser.add_argument("--delete-size-kib", type=float, default=16.0)
    parser.add_argument("--operations", type=int, default=12)
    parser.add_argument(
        "--pattern",
        choices=(
            "all",
            "mixed",
            "beginning",
            "middle",
            "end",
            "distributed",
            "localized",
        ),
        default="all",
    )
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--chunkers", default="fixed,fastcdc")
    parser.add_argument("--fixed-chunk-size", type=int, default=4096)
    parser.add_argument("--fastcdc-min-size", type=int, default=2048)
    parser.add_argument("--fastcdc-avg-size", type=int, default=4096)
    parser.add_argument("--fastcdc-max-size", type=int, default=8192)
    parser.add_argument("--data-shards", type=int, default=4)
    parser.add_argument("--total-shards", type=int, default=8)
    parser.add_argument("--seed", type=int, default=20261112)
    parser.add_argument(
        "--output-dir", type=Path, default=PROJECT_ROOT / "results" / "insert_delete"
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    _validate_args(args)
    patterns = (
        ("mixed", "beginning", "middle", "end", "distributed", "localized")
        if args.pattern == "all"
        else (args.pattern,)
    )
    workloads = {
        pattern: generate_insert_delete_workload(
            initial_size=int(args.initial_size_mib * MIB),
            insert_size=int(args.insert_size_kib * KIB),
            delete_size=int(args.delete_size_kib * KIB),
            operations=args.operations,
            seed=args.seed,
            pattern=pattern,
        )
        for pattern in patterns
    }
    chunker_names = _parse_chunkers(args.chunkers)

    for warmup_index in range(args.warmup):
        ordered_chunkers = (
            chunker_names if warmup_index % 2 == 0 else list(reversed(chunker_names))
        )
        for pattern in patterns:
            for name in ordered_chunkers:
                _run_one(workloads[pattern], name, -(warmup_index + 1), args)

    operation_rows: list[dict[str, Any]] = []
    sequence_rows: list[dict[str, Any]] = []
    for run_index in range(args.repeat):
        ordered_chunkers = (
            chunker_names if run_index % 2 == 0 else list(reversed(chunker_names))
        )
        for pattern in patterns:
            for name in ordered_chunkers:
                operations, sequence = _run_one(
                    workloads[pattern], name, run_index, args
                )
                operation_rows.extend(operations)
                sequence_rows.append(sequence)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    operation_export = export_results(
        output_dir=args.output_dir,
        prefix="insert_delete_operations",
        rows=operation_rows,
        summary={
            "scenario": "insert_delete",
            "workloads": {
                pattern: {
                    "seed": item.seed,
                    "initial_size_bytes": len(item.initial_data),
                    "operations": len(item.operations),
                    "inserted_bytes": item.total_inserted_bytes,
                    "deleted_bytes": item.total_deleted_bytes,
                }
                for pattern, item in workloads.items()
            },
            "environment": {"python": sys.version, "platform": platform.platform()},
        },
    )
    sequence_export = export_results(
        output_dir=args.output_dir,
        prefix="insert_delete_sequences",
        rows=sequence_rows,
        summary={
            "scenario": "insert_delete",
            "workloads": {
                pattern: {
                    "seed": item.seed,
                    "initial_size_bytes": len(item.initial_data),
                    "operations": len(item.operations),
                }
                for pattern, item in workloads.items()
            },
            "configuration": vars(args) | {"output_dir": str(args.output_dir)},
            "summary_by_chunker_and_pattern": {
                f"{name}/{pattern}": numeric_summary(
                    [
                        {
                            key: value
                            for key, value in row.items()
                            if isinstance(value, (int, float))
                            and not isinstance(value, bool)
                        }
                        for row in sequence_rows
                        if row["chunker"] == name and row["pattern"] == pattern
                    ]
                )
                for name in chunker_names
                for pattern in patterns
            },
        },
    )
    print(
        json.dumps(
            {
                "operation_csv": str(operation_export.csv_path),
                "operation_summary": str(operation_export.json_path),
                "sequence_csv": str(sequence_export.csv_path),
                "sequence_summary": str(sequence_export.json_path),
                "runs": len(sequence_rows),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
