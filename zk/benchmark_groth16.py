#!/usr/bin/env python3
"""Benchmark the real ENGRAM Groth16 PoSt circuit.

Run from the zk/ directory after the circuit setup and input generation:
    python3 benchmark_groth16.py --repeat 30 --warmup 2

Compilation, Powers of Tau, and zkey setup are excluded from timed measurements.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import platform
import random
import re
import statistics
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

R1CS = Path("build/engram_post_real_file.r1cs")
WASM = Path("build/engram_post_real_file_js/engram_post_real_file.wasm")
GENERATOR = Path("build/engram_post_real_file_js/generate_witness.js")
ZKEY = Path("build/engram_post_real_file_final.zkey")
VKEY = Path("build/engram_post_real_file_verification_key.json")
INPUT = Path("input_real_file.json")
BOOTSTRAP_SAMPLES = 2000


def command(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, cwd=cwd, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, check=False)
    if result.returncode:
        raise RuntimeError(f"Command failed: {' '.join(args)}\n{result.stdout}")
    return result


def snarkjs(*args: str) -> list[str]:
    # The project-local snarkjs CLI is invoked with npx; process startup is
    # included consistently in prove/verify timings.
    return ["npx", "--no-install", "snarkjs", *args]


def elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000.0


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    rank = max(1, min(len(ordered), int((p / 100) * len(ordered) + 0.999999)))
    return ordered[rank - 1]


def median_ci(values: list[float], seed: int) -> tuple[float, float]:
    if len(values) == 1:
        return values[0], values[0]
    rng = random.Random(seed)
    medians = [statistics.median(rng.choices(values, k=len(values)))
               for _ in range(BOOTSTRAP_SAMPLES)]
    return percentile(medians, 2.5), percentile(medians, 97.5)


def required(root: Path, path: Path) -> Path:
    full = root / path
    if not full.is_file():
        raise FileNotFoundError(
            f"Missing required artifact: {full}\n"
            "Run the existing real-file PoSt pipeline/setup first."
        )
    return full


def read_constraint_count(root: Path) -> tuple[int, str]:
    info = command(snarkjs("r1cs", "info", str(R1CS)), root).stdout
    match = re.search(r"(?:#\s*of\s*)?constraints\s*:\s*(\d+)", info, re.I)
    if not match:
        raise RuntimeError(f"Cannot parse constraints from snarkjs r1cs info:\n{info}")
    return int(match.group(1)), info.strip()


def one_run(root: Path, input_path: Path, artifacts: Path, number: int,
            run_type: str, input_hash: str, input_bytes: int,
            constraints: int, sampled_shards: int, chunks_per_shard: int,
            source_size_bytes: int | None) -> dict:
    run_dir = artifacts / f"{run_type}_{number:03d}"
    run_dir.mkdir(parents=True, exist_ok=False)
    witness = (run_dir / "witness.wtns").resolve()
    proof = (run_dir / "proof.json").resolve()
    public = (run_dir / "public.json").resolve()

    start = time.perf_counter()
    command(["node", str(GENERATOR), str(WASM), str(input_path), str(witness)], root)
    witness_ms = elapsed_ms(start)
    if not witness.is_file() or witness.stat().st_size == 0:
        raise RuntimeError(f"Witness file missing/empty: {witness}")

    start = time.perf_counter()
    command(snarkjs("groth16", "prove", str(ZKEY), str(witness),
                    str(proof), str(public)), root)
    prove_ms = elapsed_ms(start)
    if not proof.is_file() or not public.is_file():
        raise RuntimeError("snarkjs prove did not create proof/public JSON.")

    start = time.perf_counter()
    verify_out = command(snarkjs("groth16", "verify", str(VKEY),
                                 str(public), str(proof)), root).stdout
    verify_ms = elapsed_ms(start)
    valid = "OK!" in verify_out.upper()
    if not valid:
        raise RuntimeError(f"Groth16 verification did not report OK!:\n{verify_out}")

    proof_obj = json.loads(proof.read_text(encoding="utf-8"))
    public_obj = json.loads(public.read_text(encoding="utf-8"))
    compact_size = len(json.dumps(proof_obj, separators=(",", ":"),
                                  ensure_ascii=False).encode("utf-8"))
    return {
        "run_id": uuid.uuid4().hex[:12],
        "run_type": run_type,
        "repeat": number,
        "input_sha256": input_hash,
        "input_json_size_bytes": input_bytes,
        "source_size_bytes": source_size_bytes,
        "sampled_shard_count": sampled_shards,
        "chunks_per_sampled_shard": chunks_per_shard,
        "circuit_constraints": constraints,
        "public_input_count": len(public_obj),
        "circuit_witness_generation_ms": witness_ms,
        "witness_file_size_bytes": witness.stat().st_size,
        "groth16_proof_generation_ms": prove_ms,
        "groth16_verification_ms": verify_ms,
        # Compact JSON size, not a custom binary/on-chain proof encoding.
        "proof_size_bytes": compact_size,
        "proof_json_file_size_bytes": proof.stat().st_size,
        "proof_success": valid,
        "witness_artifact": str(witness),
        "proof_artifact": str(proof),
        "public_artifact": str(public),
    }


def summarize(rows: list[dict]) -> dict:
    result = {"runs": len(rows)}
    metrics = [
        "circuit_witness_generation_ms", "groth16_proof_generation_ms",
        "groth16_verification_ms", "proof_size_bytes", "proof_json_file_size_bytes",
        "witness_file_size_bytes", "circuit_constraints", "input_json_size_bytes",
        "public_input_count", "source_size_bytes", "sampled_shard_count",
        "chunks_per_sampled_shard",
    ]
    for i, name in enumerate(metrics):
        values = [float(r[name]) for r in rows if isinstance(r.get(name), (int, float))
                  and not isinstance(r.get(name), bool)]
        if not values:
            continue
        result[f"{name}_median"] = statistics.median(values)
        result[f"{name}_p95"] = percentile(values, 95)
        result[f"{name}_min"] = min(values)
        result[f"{name}_max"] = max(values)
        low, high = median_ci(values, 20261022 + i)
        result[f"{name}_median_ci95_low"] = low
        result[f"{name}_median_ci95_high"] = high
    result["proof_success_all_passed"] = all(r["proof_success"] for r in rows)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark ENGRAM Groth16 PoSt metrics.")
    parser.add_argument("--repeat", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--input", type=Path, default=INPUT)
    parser.add_argument(
        "--source-size-bytes", type=int, default=None,
        help="Original plaintext file size in bytes (recorded for comparison across workloads)",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("results/groth16"))
    parser.add_argument("--artifact-dir", type=Path, default=Path("build/groth16_benchmark_artifacts"))
    args = parser.parse_args()
    if args.repeat < 1 or args.warmup < 0:
        parser.error("--repeat must be >= 1 and --warmup must be >= 0")

    root = Path.cwd().resolve()
    input_path = required(root, args.input)
    for p in (R1CS, WASM, GENERATOR, ZKEY, VKEY):
        required(root, p)
    input_data = json.loads(input_path.read_text(encoding="utf-8"))
    shard_chunks = input_data.get("shardChunks")
    if not isinstance(shard_chunks, list) or not shard_chunks:
        parser.error("Input JSON must contain a non-empty `shardChunks` array.")
    if not isinstance(shard_chunks[0], list) or not shard_chunks[0]:
        parser.error("Each `shardChunks` entry must be a non-empty list.")
    chunks_per_shard = len(shard_chunks[0])
    if any(not isinstance(chunks, list) or len(chunks) != chunks_per_shard
           for chunks in shard_chunks):
        parser.error("All sampled shards must have the same number of chunks.")
    if args.source_size_bytes is not None and args.source_size_bytes < 0:
        parser.error("--source-size-bytes cannot be negative")

    input_hash = hashlib.sha256(input_path.read_bytes()).hexdigest()
    input_bytes = input_path.stat().st_size
    sampled_shards = len(shard_chunks)
    constraints, r1cs_info = read_constraint_count(root)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = uuid.uuid4().hex[:6]
    artifacts = (root / args.artifact_dir / f"{stamp}_{suffix}").resolve()
    artifacts.mkdir(parents=True, exist_ok=False)
    print(f"Input: {input_path} ({input_bytes} JSON bytes)")
    print(f"Input SHA-256: {input_hash}")
    print(f"Circuit constraints: {constraints}")
    print(f"Sampled shards / chunks per sampled shard: {sampled_shards} / {chunks_per_shard}")
    if args.source_size_bytes is not None:
        print(f"Original plaintext size: {args.source_size_bytes} bytes")
    print(f"Warm-up={args.warmup}, measured runs={args.repeat}")
    print("Compilation, input preparation, and Groth16 setup are excluded.")

    for i in range(1, args.warmup + 1):
        row = one_run(root, input_path, artifacts, i, "warmup",
                      input_hash, input_bytes, constraints, sampled_shards,
                      chunks_per_shard, args.source_size_bytes)
        print(f"[warmup {i}/{args.warmup}] witness={row['circuit_witness_generation_ms']:.2f} ms; "
              f"prove={row['groth16_proof_generation_ms']:.2f} ms; "
              f"verify={row['groth16_verification_ms']:.2f} ms; valid={row['proof_success']}")

    rows = []
    for i in range(1, args.repeat + 1):
        row = one_run(root, input_path, artifacts, i, "measurement",
                      input_hash, input_bytes, constraints, sampled_shards,
                      chunks_per_shard, args.source_size_bytes)
        rows.append(row)
        print(f"[{i}/{args.repeat}] witness={row['circuit_witness_generation_ms']:.2f} ms; "
              f"prove={row['groth16_proof_generation_ms']:.2f} ms; "
              f"verify={row['groth16_verification_ms']:.2f} ms; "
              f"proof={row['proof_size_bytes']} B; valid={row['proof_success']}")

    output_dir = (root / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"groth16_{stamp}_{suffix}"
    csv_path = output_dir / f"{stem}_runs.csv"
    json_path = output_dir / f"{stem}_summary.json"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    node_version = subprocess.run(["node", "--version"], cwd=root, text=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT).stdout.strip()
    snarkjs_version = subprocess.run(snarkjs("--version"), cwd=root, text=True,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT).stdout.strip()
    summary = {
        "config": {
            "repeat": args.repeat, "warmup": args.warmup,
            "input_file": str(input_path), "input_sha256": input_hash,
            "source_size_bytes": args.source_size_bytes,
            "sampled_shard_count": sampled_shards,
            "chunks_per_sampled_shard": chunks_per_shard,
            "r1cs_file": str(root / R1CS), "zkey_file": str(root / ZKEY),
            "verification_key_file": str(root / VKEY),
            "excluded_from_timing": [
                "input/pipeline generation", "Circom compilation",
                "Powers of Tau and zkey setup", "R1CS inspection",
            ],
        },
        "environment": {
            "python_version": sys.version, "node_version": node_version,
            "snarkjs_version_output": snarkjs_version, "platform": platform.platform(),
        },
        "circuit": {"constraints": constraints, "r1cs_info_output": r1cs_info},
        "metric_definitions": {
            "circuit_witness_generation_ms": "Wall time for generate_witness.js, including Node startup.",
            "groth16_proof_generation_ms": "Wall time for snarkjs groth16 prove, including CLI startup and artifact writing.",
            "groth16_verification_ms": "Wall time for snarkjs groth16 verify, including CLI startup and input reads.",
            "proof_size_bytes": "Compact UTF-8 JSON serialization size of proof.json; not binary/on-chain encoding.",
            "proof_json_file_size_bytes": "Actual proof.json file size on disk.",
            "witness_file_size_bytes": "Actual .wtns witness file size on disk.",
            "circuit_constraints": "Total constraints reported by snarkjs r1cs info.",
        },
        "summary": summarize(rows),
        "raw_csv": str(csv_path),
        "artifacts_directory": str(artifacts),
    }
    json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nRaw measurements: {csv_path}")
    print(f"Summary + definitions: {json_path}")
    print(f"Artifacts: {artifacts}")


if __name__ == "__main__":
    main()
