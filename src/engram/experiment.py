#!/usr/bin/env python3
"""Repeatable baseline benchmark for the current ENGRAM simulation pipeline.

Run from the repository root, for example:
  PYTHONPATH=src python scripts/benchmark_pipeline.py test_data/simple.txt --repeat 30 --warmup 2

Measures the Python simulation only. Groth16 witness/prove/verify timings are
measured separately by zk/benchmark_groth16.py.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import random
import statistics
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engram.challenge import create_challenge
from engram.encryption import EncryptedPayload, decrypt, encrypt, generate_key
from engram.erasure_coding import decode, encode
from engram.proof import generate_proof, verify_storage_proof
from engram.storage import StorageManager

BOOTSTRAP_SAMPLES = 2000


def elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000.0


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    rank = max(1, min(len(ordered), int((p / 100.0) * len(ordered) + 0.999999)))
    return ordered[rank - 1]


def median_ci(values: list[float], seed: int) -> tuple[float, float]:
    if len(values) <= 1:
        return values[0], values[0]
    rng = random.Random(seed)
    samples = [statistics.median(rng.choices(values, k=len(values))) for _ in range(BOOTSTRAP_SAMPLES)]
    return percentile(samples, 2.5), percentile(samples, 97.5)


def one_run(file_path: Path, payload: bytes, repeat: int, run_type: str,
            data_shards: int, total_shards: int, challenge_size: int) -> dict[str, Any]:
    run_id = uuid.uuid4().hex[:12]
    digest = hashlib.sha256(payload).hexdigest()
    end_to_end_start = time.perf_counter()

    with tempfile.TemporaryDirectory(prefix="engram_baseline_") as temp_dir:
        storage = StorageManager(Path(temp_dir) / "storage", node_count=total_shards)

        start = time.perf_counter()
        key = generate_key()
        key_generation_ms = elapsed_ms(start)

        start = time.perf_counter()
        encrypted = encrypt(payload, key)
        encryption_ms = elapsed_ms(start)

        start = time.perf_counter()
        encoded = encode(encrypted.ciphertext, data_shards=data_shards, total_shards=total_shards)
        erasure_encoding_ms = elapsed_ms(start)

        # Current StorageManager builds per-shard chunk Merkle trees and the
        # global shard-root tree during store_chunk; report these together.
        start = time.perf_counter()
        storage.store_chunk("payload", encoded)
        storage_write_and_commitment_ms = elapsed_ms(start)

        start = time.perf_counter()
        available = storage.load_available_shards("payload")
        storage_read_ms = elapsed_ms(start)

        metadata = storage.get_chunk_metadata("payload")
        start = time.perf_counter()
        restored_ciphertext = decode(
            available,
            original_size=metadata.original_size,
            data_shards=data_shards,
            total_shards=total_shards,
        )
        reconstruction_ms = elapsed_ms(start)

        start = time.perf_counter()
        restored_payload = decrypt(EncryptedPayload(ciphertext=restored_ciphertext, nonce=encrypted.nonce), key)
        decryption_ms = elapsed_ms(start)
        reconstruction_success = restored_payload == payload

        chunk_counts = storage.get_chunk_counts("payload")
        challenge_start = time.perf_counter()
        challenge = create_challenge(
            chunk_id="payload",
            total_shards=total_shards,
            chunks_per_shard=chunk_counts,
            k=challenge_size,
            block_hash=digest,
            miner_id="engram-baseline-benchmark",
            epoch_id="fixed-baseline-epoch",
        )
        challenge_generation_ms = elapsed_ms(challenge_start)

        start = time.perf_counter()
        storage_proof = generate_proof(
            storage=storage,
            chunk_id="payload",
            p=challenge.p,
            total_shards=total_shards,
            k=challenge_size,
        )
        python_merkle_proof_generation_ms = elapsed_ms(start)

        start = time.perf_counter()
        proof_success = verify_storage_proof(
            proof=storage_proof,
            expected_root=storage.get_merkle_root("payload"),
            expected_p=challenge.p,
            total_shards=total_shards,
            k=challenge_size,
            chunks_per_shard=chunk_counts,
        )
        python_merkle_verification_ms = elapsed_ms(start)

        stored_payload_bytes = storage.get_stored_bytes()
        end_to_end_ms = elapsed_ms(end_to_end_start)

        return {
            "run_id": run_id,
            "run_type": run_type,
            "repeat": repeat,
            "scenario": "full_pipeline_baseline",
            "source_file": str(file_path),
            "input_sha256": digest,
            "input_size_bytes": len(payload),
            "ciphertext_size_bytes": len(encrypted.ciphertext),
            "data_shards": data_shards,
            "total_shards": total_shards,
            "shard_size_bytes": len(encoded.shards[0]),
            "stored_payload_bytes": stored_payload_bytes,
            "storage_overhead_ratio": stored_payload_bytes / len(payload) if payload else None,
            "available_shards": len(available),
            "chunks_per_shard": chunk_counts[0],
            "challenge_shard_indices": ";".join(map(str, challenge.shard_indices)),
            "challenge_chunk_indices": ";".join(map(str, challenge.chunk_indices)),
            "key_generation_ms": key_generation_ms,
            "encryption_ms": encryption_ms,
            "erasure_encoding_ms": erasure_encoding_ms,
            "storage_write_and_commitment_ms": storage_write_and_commitment_ms,
            "storage_read_ms": storage_read_ms,
            "reconstruction_ms": reconstruction_ms,
            "decryption_ms": decryption_ms,
            "challenge_generation_ms": challenge_generation_ms,
            "python_merkle_proof_generation_ms": python_merkle_proof_generation_ms,
            "python_merkle_verification_ms": python_merkle_verification_ms,
            "reconstruction_success": reconstruction_success,
            "python_merkle_proof_success": proof_success,
            "pipeline_end_to_end_ms": end_to_end_ms,
            "proof_scope": "Python two-level Merkle simulation; not Groth16",
        }


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault((row["source_file"], row["run_type"]), []).append(row)
    output = []
    for group_index, ((source_file, run_type), group) in enumerate(groups.items()):
        item: dict[str, Any] = {"source_file": source_file, "run_type": run_type, "runs": len(group)}
        keys = sorted({k for row in group for k, v in row.items()
                       if isinstance(v, (int, float)) and not isinstance(v, bool)
                       and k not in {"repeat"}})
        for i, key in enumerate(keys):
            values = [float(row[key]) for row in group if isinstance(row.get(key), (int, float)) and not isinstance(row.get(key), bool)]
            if not values:
                continue
            item[f"{key}_median"] = statistics.median(values)
            item[f"{key}_p95"] = percentile(values, 95)
            item[f"{key}_min"] = min(values)
            item[f"{key}_max"] = max(values)
            lo, hi = median_ci(values, 20261022 + group_index * 1000 + i)
            item[f"{key}_median_ci95_low"] = lo
            item[f"{key}_median_ci95_high"] = hi
        for key in ("reconstruction_success", "python_merkle_proof_success"):
            item[f"{key}_all_passed"] = all(row.get(key) is True for row in group)
        output.append(item)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark the current ENGRAM Python storage pipeline baseline.")
    parser.add_argument("files", nargs="+", help="Input files to benchmark")
    parser.add_argument("--repeat", type=int, default=30, help="Measured runs per file (default: 30)")
    parser.add_argument("--warmup", type=int, default=2, help="Warm-up runs per file (excluded from summary)")
    parser.add_argument("--data-shards", type=int, default=4)
    parser.add_argument("--total-shards", type=int, default=8)
    parser.add_argument("--challenge-size", type=int, default=3)
    parser.add_argument("--output-dir", type=Path, default=Path("results/pipeline"))
    args = parser.parse_args()
    if args.repeat < 1 or args.warmup < 0:
        parser.error("--repeat must be >= 1 and --warmup must be >= 0")
    if not (1 <= args.data_shards < args.total_shards <= 255):
        parser.error("Require 1 <= data-shards < total-shards <= 255")
    if not (1 <= args.challenge_size <= args.total_shards):
        parser.error("--challenge-size must be between 1 and total-shards")

    rows: list[dict[str, Any]] = []
    files_metadata = []
    for raw in args.files:
        path = Path(raw).expanduser().resolve()
        if not path.is_file():
            parser.error(f"Input file does not exist: {path}")
        payload = path.read_bytes()
        if not payload:
            parser.error(f"Input file is empty: {path}")
        digest = hashlib.sha256(payload).hexdigest()
        files_metadata.append({"path": str(path), "size_bytes": len(payload), "sha256": digest})
        print(f"\n=== {path} ({len(payload)} bytes; sha256={digest}) ===")
        for i in range(1, args.warmup + 1):
            one_run(path, payload, i, "warmup", args.data_shards, args.total_shards, args.challenge_size)
            print(f"[warmup {i}/{args.warmup}] completed (excluded from CSV/summary)")
        for i in range(1, args.repeat + 1):
            row = one_run(path, payload, i, "measurement", args.data_shards, args.total_shards, args.challenge_size)
            rows.append(row)
            print(f"[{i}/{args.repeat}] e2e={row['pipeline_end_to_end_ms']:.2f} ms; "
                  f"store+Merkle={row['storage_write_and_commitment_ms']:.2f} ms; "
                  f"Python proof valid={row['python_merkle_proof_success']}")
            if not row["reconstruction_success"] or not row["python_merkle_proof_success"]:
                raise RuntimeError(f"Correctness check failed in measurement run {i} for {path}")

    out_dir = args.output_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = uuid.uuid4().hex[:6]
    stem = f"pipeline_baseline_{stamp}_{suffix}"
    csv_path, json_path = out_dir / f"{stem}_runs.csv", out_dir / f"{stem}_summary.json"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    summary = {
        "config": {"repeat": args.repeat, "warmup": args.warmup, "data_shards": args.data_shards,
                   "total_shards": args.total_shards, "challenge_size": args.challenge_size,
                   "timer": "time.perf_counter", "input_files": files_metadata,
                   "excluded_from_timing": ["Python interpreter startup", "input file read before each run",
                                             "Groth16 compilation/setup/proof/verification"]},
        "environment": {"python_version": sys.version, "platform": platform.platform()},
        "metric_definitions": {
            "storage_write_and_commitment_ms": "StorageManager.store_chunk: writes shard payloads and manifest, builds per-shard chunk Merkle trees and the global shard-root tree.",
            "stored_payload_bytes": "Sum of shard file payload sizes; excludes manifests, filesystem metadata, and physical disk overhead.",
            "storage_overhead_ratio": "stored_payload_bytes / original plaintext bytes.",
            "python_merkle_proof_generation_ms": "Time to collect selected chunks and both Merkle paths in Python; not Circom witness generation.",
            "python_merkle_verification_ms": "Python simulated proof verification; not Groth16 verification.",
            "pipeline_end_to_end_ms": "Includes temporary directory and StorageManager initialization plus all measured pipeline stages.",
        },
        "summary": summarize(rows), "raw_csv": str(csv_path),
    }
    json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nRaw measurements: {csv_path}\nSummary: {json_path}")


if __name__ == "__main__":
    main()
