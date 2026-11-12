"""Compare final-epoch Groth16 PoSt for Full Snapshot and Incremental storage.

The complete update sequence is written first. Exactly one challenge is then
created for each strategy, followed by one Groth16 proof per challenged target.
Circuit compilation and trusted setup are cached and reported separately from
witness/proving/verification timings.

Run from the repository root:
    PYTHONPATH=src python -m benchmarks.scenarios.groth16_comparison \
        --initial-size-mib 1 --delta-size-mib 0.1 --updates 5 \
        --batch-size 2 --repeat 3 --warmup 1

Requires circom, node, npx, zk/node_modules, and a Powers of Tau file. Set
PTAU_PATH to override the default ptau path.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import statistics
import subprocess
import hashlib
import sys
import tempfile
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Sequence

from engram.incremental.types import GenerationRecord

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from benchmarks.common.workload import generate_update_workload
from engram.challenge import create_challenge
from engram.encryption import encrypt, generate_key
from engram.erasure_coding import encode
from engram.groth16_verifier import (
    Groth16ProofArtifact,
    RegisteredGroth16Commitment,
    verify_groth16_challenge,
)
from engram.incremental.challenge import (
    GenerationLayout,
    create_incremental_challenge,
)
from engram.incremental.store import IncrementalStore
from engram.proof import ShardChallenge
from engram.storage import StorageManager
from zk.generate_circuit import generate_circuit
from zk.input_builder import build_and_save_single_chunk_input

MIB = 1024 * 1024
ZK_DIR = PROJECT_ROOT / "zk"
TEMPLATE = ZK_DIR / "circuits" / "engram_shard_post_template.circom"
BLOCK_HASH = "engram-groth16-benchmark-block"
MINER_ID = "engram-groth16-benchmark-miner"
EPOCH_ID = "engram-groth16-benchmark-epoch"


def _now_ms() -> float:
    return time.perf_counter() * 1000.0


def _timed(
    function: Callable[..., Any], *args: Any, **kwargs: Any
) -> tuple[Any, float]:
    start = _now_ms()
    result = function(*args, **kwargs)
    return result, _now_ms() - start


def _run(command: list[str], *, cwd: Path) -> float:
    start = _now_ms()
    subprocess.run(command, cwd=cwd, check=True, text=True, capture_output=True)
    return _now_ms() - start


def _require_tools() -> None:
    for name in ("circom", "node", "npx"):
        if shutil.which(name) is None:
            raise RuntimeError(f"Required executable not found on PATH: {name}")
    if not (ZK_DIR / "node_modules" / "snarkjs").exists():
        raise RuntimeError("zk/node_modules/snarkjs not found; run npm install in zk/")
    if not TEMPLATE.is_file():
        raise FileNotFoundError(TEMPLATE)


def _validate(args: argparse.Namespace) -> None:
    for name in ("initial_size_mib", "delta_size_mib"):
        value = getattr(args, name)
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"--{name.replace('_', '-')} must be finite and > 0")
    if args.updates < 0 or args.batch_size < 1:
        raise ValueError("--updates must be >= 0 and --batch-size >= 1")
    if args.repeat < 1 or args.warmup < 0:
        raise ValueError("--repeat must be >= 1 and --warmup >= 0")
    if not (1 <= args.data_shards < args.total_shards <= 255):
        raise ValueError("Require 1 <= data-shards < total-shards <= 255")
    if args.chunk_size <= 0 or args.k <= 0:
        raise ValueError("--chunk-size and --k must be positive")
    if args.k > args.total_shards:
        raise ValueError("--k must not exceed --total-shards")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    keys: list[str] = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def _summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for strategy in ("full_snapshot", "incremental"):
        selected = [r for r in rows if r["strategy"] == strategy and not r["warmup"]]
        if not selected:
            continue
        numeric_keys = [
            key
            for key, value in selected[0].items()
            if isinstance(value, (int, float))
            and not isinstance(value, bool)
            and key not in {"run_index"}
        ]
        record: dict[str, Any] = {"strategy": strategy, "runs": len(selected)}
        for key in numeric_keys:
            values = [float(r[key]) for r in selected if r.get(key) is not None]
            if values:
                record[f"{key}_mean"] = statistics.mean(values)
                record[f"{key}_median"] = statistics.median(values)
        output.append(record)
    return output


def _circuit_cache(
    cache_dir: Path,
    shard_bytes_len: int,
    n_shards: int,
    ptau_path: Path,
) -> tuple[dict[str, Path], dict[str, float]]:
    """Compile/setup once per circuit shape; return reusable artifacts."""
    template_stat = TEMPLATE.stat()
    ptau_stat = ptau_path.stat()
    fingerprint_payload = (
        f"{shard_bytes_len}:{n_shards}:"
        f"{template_stat.st_size}:{template_stat.st_mtime_ns}:"
        f"{ptau_path.resolve()}:{ptau_stat.st_size}:{ptau_stat.st_mtime_ns}"
    ).encode("utf-8")
    fingerprint = hashlib.sha256(fingerprint_payload).hexdigest()[:10]
    shape = f"shard{shard_bytes_len}_n{n_shards}_{fingerprint}"
    shape_dir = cache_dir / shape
    build_dir = shape_dir / "build"
    circuit_dir = shape_dir / "circuits"
    circuit_name = f"engram_post_{shape}"
    generated = circuit_dir / f"{circuit_name}.circom"
    r1cs = build_dir / f"{circuit_name}.r1cs"
    wasm = build_dir / f"{circuit_name}_js" / f"{circuit_name}.wasm"
    witness_generator = build_dir / f"{circuit_name}_js" / "generate_witness.js"
    zkey = build_dir / f"{circuit_name}.zkey"
    vk = build_dir / f"{circuit_name}_verification_key.json"

    if (
        r1cs.is_file()
        and wasm.is_file()
        and witness_generator.is_file()
        and zkey.is_file()
        and vk.is_file()
    ):
        return {
            "r1cs": r1cs,
            "wasm": wasm,
            "witness_generator": witness_generator,
            "zkey": zkey,
            "vk": vk,
        }, {
            "compile_ms": 0.0,
            "setup_ms": 0.0,
            "cache_hit": 1.0,
        }

    circuit_dir.mkdir(parents=True, exist_ok=True)
    build_dir.mkdir(parents=True, exist_ok=True)
    generate_circuit(
        template_path=TEMPLATE,
        output_path=generated,
        shard_bytes_len=shard_bytes_len,
        n_shards=n_shards,
    )
    compile_ms = _run(
        [
            "circom",
            str(generated),
            "--r1cs",
            "--wasm",
            "--sym",
            "-l",
            str(ZK_DIR / "node_modules"),
            "-o",
            str(build_dir),
        ],
        cwd=PROJECT_ROOT,
    )
    if not (r1cs.is_file() and wasm.is_file() and witness_generator.is_file()):
        raise RuntimeError(f"Circom did not produce expected artifacts for {shape}")

    setup_ms = _run(
        [
            "npx",
            "--no-install",
            "snarkjs",
            "groth16",
            "setup",
            str(r1cs),
            str(ptau_path),
            str(zkey),
        ],
        cwd=ZK_DIR,
    )
    setup_ms += _run(
        [
            "npx",
            "--no-install",
            "snarkjs",
            "zkey",
            "export",
            "verificationkey",
            str(zkey),
            str(vk),
        ],
        cwd=ZK_DIR,
    )
    return {
        "r1cs": r1cs,
        "wasm": wasm,
        "witness_generator": witness_generator,
        "zkey": zkey,
        "vk": vk,
    }, {
        "compile_ms": compile_ms,
        "setup_ms": setup_ms,
        "cache_hit": 0.0,
    }


def _prepare_full_snapshot(
    root: Path,
    final_data: bytes,
    args: argparse.Namespace,
    commitment_id: str,
) -> tuple[StorageManager, str, int, int, float]:
    storage = StorageManager(root / "storage", node_count=args.total_shards)
    start = _now_ms()
    key = generate_key()
    encrypted = encrypt(final_data, key)
    encoded = encode(
        encrypted.ciphertext,
        data_shards=args.data_shards,
        total_shards=args.total_shards,
    )
    storage.store_chunk(commitment_id, encoded)
    return (
        storage,
        commitment_id,
        *storage.get_shard_layout(commitment_id),
        _now_ms() - start,
    )


def _prepare_incremental(
    root: Path,
    initial_data: bytes,
    deltas: Sequence[bytes],
    args: argparse.Namespace,
) -> tuple[IncrementalStore, str, list[GenerationRecord], float]:
    object_id = f"groth16-incremental-{uuid.uuid4().hex[:12]}"
    store = IncrementalStore(
        root / "incremental_store",
        data_shards=args.data_shards,
        total_shards=args.total_shards,
        chunk_size=args.chunk_size,
        size_threshold=len(initial_data) + sum(map(len, deltas)) + 1,
        max_wait_seconds=1e12,
    )
    start = _now_ms()
    store.append(object_id, initial_data)
    if not store.seal(object_id):
        raise RuntimeError("Failed to seal initial generation")
    for delta_index, delta in enumerate(deltas, start=1):
        store.append(object_id, delta)
        if delta_index % args.batch_size == 0 or delta_index == len(deltas):
            if not store.seal(object_id):
                raise RuntimeError(
                    f"Failed to seal generation after update {delta_index}"
                )
    generations = store.list_generations(object_id)
    if not generations:
        raise RuntimeError("Incremental sequence produced no committed generations")
    return store, object_id, generations, _now_ms() - start


def _challenge_targets(
    strategy: str,
    storage: StorageManager,
    commitment_id: str,
    generations: list[GenerationRecord] | None,
    args: argparse.Namespace,
) -> tuple[bytes, tuple[ShardChallenge, ...], float, list[dict[str, Any]]]:
    start = _now_ms()
    layout_rows: list[dict[str, Any]] = []
    if strategy == "full_snapshot":
        counts = tuple(storage.get_chunk_counts(commitment_id))
        challenge = create_challenge(
            chunk_id=commitment_id,
            total_shards=args.total_shards,
            chunks_per_shard=counts,
            k=args.k,
            block_hash=BLOCK_HASH,
            miner_id=MINER_ID,
            epoch_id=EPOCH_ID,
        )
        targets = tuple(
            ShardChallenge(commitment_id, shard_index, chunk_index)
            for shard_index, chunk_index in zip(
                challenge.shard_indices, challenge.chunk_indices
            )
        )
        layout_rows.append(
            {"generation_id": commitment_id, "chunks_per_shard": list(counts)}
        )
        p = challenge.p
    else:
        assert generations is not None
        layouts: list[GenerationLayout] = []
        for generation in generations:
            generation_id = str(generation["generation_id"])
            counts = tuple(storage.get_chunk_counts(generation_id))
            layouts.append(GenerationLayout(generation_id, counts))
            layout_rows.append(
                {"generation_id": generation_id, "chunks_per_shard": list(counts)}
            )
        challenge = create_incremental_challenge(
            layouts=tuple(layouts),
            k=args.k,
            block_hash=BLOCK_HASH,
            miner_id=MINER_ID,
            epoch_id=EPOCH_ID,
        )
        targets = tuple(
            ShardChallenge(item.generation_id, item.shard_index, item.chunk_index)
            for item in challenge.challenged_shards
        )
        p = challenge.p
    return p, targets, _now_ms() - start, layout_rows


def _run_post(
    strategy: str,
    storage: StorageManager,
    commitment_ids: set[str],
    targets: tuple[ShardChallenge, ...],
    p: bytes,
    run_dir: Path,
    cache_dir: Path,
    ptau_path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not targets:
        raise RuntimeError(f"{strategy}: challenge returned no targets")
    run_dir.mkdir(parents=True, exist_ok=True)
    target_rows: list[dict[str, Any]] = []
    artifacts: list[Groth16ProofArtifact] = []
    registered: dict[str, RegisteredGroth16Commitment] = {}
    shape_metrics: dict[tuple[int, int], dict[str, float]] = {}
    shape_artifacts: dict[tuple[int, int], dict[str, Path]] = {}

    input_total = witness_total = prove_total = verify_total = 0.0
    proof_bytes_total = 0

    for index, target in enumerate(targets):
        commitment_id = target.commitment_id
        shard_bytes_len, n_shards = storage.get_shard_layout(commitment_id)
        shape = (shard_bytes_len, n_shards)
        if shape not in shape_artifacts:
            files, metrics = _circuit_cache(
                cache_dir, shard_bytes_len, n_shards, ptau_path
            )
            shape_artifacts[shape] = files
            shape_metrics[shape] = metrics
        files = shape_artifacts[shape]

        root = storage.get_merkle_root(commitment_id)
        previous = registered.get(commitment_id)
        if previous is None:
            registered[commitment_id] = RegisteredGroth16Commitment(
                merkle_root=root,
                verification_key_path=files["vk"],
            )
        elif (
            previous.merkle_root != root
            or previous.verification_key_path != files["vk"]
        ):
            raise RuntimeError(
                "Commitment has inconsistent root or circuit registration"
            )

        target_dir = run_dir / f"target_{index:03d}"
        target_dir.mkdir(parents=True, exist_ok=True)
        input_path = target_dir / "input.json"
        _, input_ms = _timed(
            build_and_save_single_chunk_input,
            storage=storage,
            chunk_id=commitment_id,
            shard_index=target.shard_index,
            chunk_index=target.chunk_index,
            output_path=input_path,
        )
        input_total += input_ms

        witness_path = target_dir / "witness.wtns"
        witness_ms = _run(
            [
                "node",
                str(files["witness_generator"]),
                str(files["wasm"]),
                str(input_path),
                str(witness_path),
            ],
            cwd=ZK_DIR,
        )
        witness_total += witness_ms

        proof_path = target_dir / "proof.json"
        public_path = target_dir / "public.json"
        prove_ms = _run(
            [
                "npx",
                "--no-install",
                "snarkjs",
                "groth16",
                "prove",
                str(files["zkey"]),
                str(witness_path),
                str(proof_path),
                str(public_path),
            ],
            cwd=ZK_DIR,
        )
        prove_total += prove_ms
        proof_bytes = proof_path.stat().st_size
        proof_bytes_total += proof_bytes
        artifacts.append(Groth16ProofArtifact(target, p, proof_path, public_path))
        target_rows.append(
            {
                "strategy": strategy,
                "target_index": index,
                "commitment_id": commitment_id,
                "shard_index": target.shard_index,
                "chunk_index": target.chunk_index,
                "shard_bytes_len": shard_bytes_len,
                "n_shards": n_shards,
                "input_prepare_ms": input_ms,
                "witness_ms": witness_ms,
                "prove_ms": prove_ms,
                "proof_size_bytes": proof_bytes,
                "circuit_compile_ms": shape_metrics[shape]["compile_ms"],
                "trusted_setup_ms": shape_metrics[shape]["setup_ms"],
                "circuit_cache_hit": bool(shape_metrics[shape]["cache_hit"]),
            }
        )

    verify_start = _now_ms()
    result = verify_groth16_challenge(
        challenge_targets=targets,
        proofs=tuple(artifacts),
        registered_commitments=registered,
        expected_p=p,
        cwd=ZK_DIR,
    )
    verify_total = _now_ms() - verify_start
    if not result.valid:
        raise RuntimeError(
            f"{strategy}: Groth16 verification failed; "
            f"verified={result.verified_count}/{result.expected_count}, "
            f"failed={result.failed_targets}"
        )

    setup_unique = {shape: metrics for shape, metrics in shape_metrics.items()}
    metrics = {
        "proof_count": len(targets),
        "distinct_commitments_challenged": len({t.commitment_id for t in targets}),
        "distinct_circuit_shapes": len(shape_artifacts),
        "input_prepare_total_ms": input_total,
        "witness_total_ms": witness_total,
        "prove_total_ms": prove_total,
        "prove_mean_ms": prove_total / len(targets),
        "verify_all_ms": verify_total,
        "proof_bytes_total": proof_bytes_total,
        "proof_bytes_mean": proof_bytes_total / len(targets),
        "verification_valid": result.valid,
        "verified_count": result.verified_count,
        "compile_ms_for_uncached_shapes": sum(
            x["compile_ms"] for x in setup_unique.values()
        ),
        "trusted_setup_ms_for_uncached_shapes": sum(
            x["setup_ms"] for x in setup_unique.values()
        ),
    }
    return metrics, target_rows


def _run_strategy(
    strategy: str,
    initial_data: bytes,
    deltas: Sequence[bytes],
    args: argparse.Namespace,
    run_index: int,
    warmup: bool,
    cache_dir: Path,
    ptau_path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    final_data = initial_data + b"".join(deltas)
    with tempfile.TemporaryDirectory(prefix=f"engram_{strategy}_") as temp:
        root = Path(temp)
        if strategy == "full_snapshot":
            storage, commitment_id, shard_len, n_shards, write_ms = (
                _prepare_full_snapshot(
                    root,
                    final_data,
                    args,
                    f"full_snapshot_{uuid.uuid4().hex[:12]}",
                )
            )
            generations = None
            commitment_ids = {commitment_id}
            generation_count = 1
            logical_bytes_processed = len(final_data)
        else:
            store, object_id, generations, write_ms = _prepare_incremental(
                root,
                initial_data,
                deltas,
                args,
            )
            storage = store.storage
            commitment_ids = {str(g["generation_id"]) for g in generations}
            generation_count = len(generations)
            logical_bytes_processed = sum(
                int(g["plaintext_size_bytes"]) for g in generations
            )
            shard_len, n_shards = (0, args.total_shards)
            commitment_id = object_id

        p, targets, challenge_ms, layouts = _challenge_targets(
            strategy,
            storage,
            commitment_id if strategy == "full_snapshot" else "",
            generations,
            args,
        )
        if any(target.commitment_id not in commitment_ids for target in targets):
            raise RuntimeError(
                "Challenge references a commitment outside the final stored state"
            )

        post_metrics, target_rows = _run_post(
            strategy,
            storage,
            commitment_ids,
            targets,
            p,
            root / "post_artifacts",
            cache_dir,
            ptau_path,
        )
        row = {
            "strategy": strategy,
            "run_index": run_index,
            "warmup": warmup,
            "final_logical_bytes": len(final_data),
            "initial_bytes": len(initial_data),
            "delta_bytes": sum(map(len, deltas)),
            "generation_count": generation_count,
            "logical_bytes_processed_for_storage": logical_bytes_processed,
            "storage_processing_amplification": logical_bytes_processed
            / len(final_data),
            "storage_write_ms": write_ms,
            "challenge_ms": challenge_ms,
            "challenge_target_count": len(targets),
            "challenge_commitment_count": len({t.commitment_id for t in targets}),
            "challenge_layouts": layouts,
            **post_metrics,
        }
        for target_row in target_rows:
            target_row.update({"run_index": run_index, "warmup": warmup})
        return row, target_rows, layouts


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Final-epoch Groth16 comparison: Full Snapshot vs Incremental"
    )
    parser.add_argument("--initial-size-mib", type=float, default=1.0)
    parser.add_argument("--delta-size-mib", type=float, default=0.1)
    parser.add_argument("--updates", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--data-shards", type=int, default=4)
    parser.add_argument("--total-shards", type=int, default=8)
    parser.add_argument("--chunk-size", type=int, default=4096)
    parser.add_argument(
        "--k",
        type=int,
        default=3,
        help="Number of challenged chunks/proofs per final epoch",
    )
    parser.add_argument("--seed", type=int, default=20261104)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "results" / "groth16_comparison",
    )
    parser.add_argument("--ptau", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    _validate(args)
    _require_tools()
    ptau_path = (
        (
            args.ptau
            or Path(
                os.getenv(
                    "PTAU_PATH",
                    str(ZK_DIR / "build" / "powersOfTau28_hez_final_16.ptau"),
                )
            )
        )
        .expanduser()
        .resolve()
    )
    if not ptau_path.is_file():
        raise FileNotFoundError(
            f"Powers of Tau file not found: {ptau_path}; pass --ptau or set PTAU_PATH"
        )

    initial_size = int(args.initial_size_mib * MIB)
    delta_size = int(args.delta_size_mib * MIB)
    if initial_size <= 0 or delta_size <= 0:
        raise ValueError("Selected MiB values are too small to create non-empty data")
    workload = generate_update_workload(
        initial_size=initial_size,
        delta_size=delta_size,
        num_deltas=args.updates,
        seed=args.seed,
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = args.output_dir / "circuit_cache"
    run_rows: list[dict[str, Any]] = []
    target_rows: list[dict[str, Any]] = []
    layout_rows: list[dict[str, Any]] = []

    total_iterations = args.warmup + args.repeat
    for iteration in range(total_iterations):
        warmup = iteration < args.warmup
        run_index = iteration - args.warmup if not warmup else iteration
        # Alternate execution order to reduce systematic thermal/order bias.
        strategies = (
            ("full_snapshot", "incremental")
            if iteration % 2 == 0
            else ("incremental", "full_snapshot")
        )
        for strategy in strategies:
            row, per_target, layouts = _run_strategy(
                strategy,
                workload.initial_data,
                workload.deltas,
                args,
                run_index,
                warmup,
                cache_dir,
                ptau_path,
            )
            run_rows.append(row)
            target_rows.extend(per_target)
            for layout in layouts:
                layout_rows.append(
                    {
                        "strategy": strategy,
                        "run_index": run_index,
                        "warmup": warmup,
                        **layout,
                    }
                )
            print(
                f"[{ 'warmup' if warmup else 'run' } {run_index}] {strategy}: "
                f"{row['proof_count']} proofs, prove={row['prove_total_ms']:.1f} ms, "
                f"verify={row['verify_all_ms']:.1f} ms, valid={row['verification_valid']}"
            )

    _write_csv(args.output_dir / "runs.csv", run_rows)
    _write_csv(args.output_dir / "targets.csv", target_rows)
    _write_csv(args.output_dir / "challenge_layouts.csv", layout_rows)
    _write_json(
        args.output_dir / "summary.json",
        {
            "configuration": {
                "initial_size_mib": args.initial_size_mib,
                "delta_size_mib": args.delta_size_mib,
                "updates": args.updates,
                "batch_size": args.batch_size,
                "data_shards": args.data_shards,
                "total_shards": args.total_shards,
                "chunk_size": args.chunk_size,
                "k": args.k,
                "repeat": args.repeat,
                "warmup": args.warmup,
                "seed": args.seed,
                "ptau_path": str(ptau_path),
                "challenge_policy": {
                    "full_snapshot": "k shards sampled from the single final commitment",
                    "incremental": "up to k generations, one shard per selected generation; if fewer generations, extra shards are sampled from selected generations",
                    "note": "Both strategies produce k challenged chunks when enough total shards exist, but their generation/shard sampling distributions differ; interpret as implementation-policy comparison, not identical sampling distribution.",
                },
                "timing_scope": "Compile and trusted setup are cached by circuit shape and reported separately; input preparation, witness generation, proving and verification are measured for each run.",
            },
            "summary": _summary(run_rows),
        },
    )
    print(f"\nResults written to: {args.output_dir}")


if __name__ == "__main__":
    main()
