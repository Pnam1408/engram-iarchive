# benchmarks/scenarios/baseline_pipeline.py
#!/usr/bin/env python3
"""Repeatable benchmark for the current ENGRAM Python storage pipeline.

This module owns baseline-pipeline orchestration and CLI behavior.
Reusable statistics are delegated to benchmarks.common.statistics.

Groth16 witness/prove/verify timings are measured separately by
zk/benchmark_groth16.py.
"""

from __future__ import annotations

import argparse
import hashlib
import platform
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from engram.challenge import create_challenge
from engram.encryption import EncryptedPayload, decrypt, encrypt, generate_key
from engram.erasure_coding import decode, encode
from engram.proof import (
    ShardChallenge,
    ShardWitness,
    generate_shard_proof,
    verify_challenge_proofs,
)
from engram.storage import StorageManager

from benchmarks.common.timing import measure_ms
from benchmarks.common.statistics import summarize_pipeline_rows
from benchmarks.common.results import export_results


def generate_challenge_shard_proofs(
    storage: StorageManager,
    challenge_targets: tuple[ShardChallenge, ...],
    p: bytes,
) -> tuple:
    """
    Generate one Python Merkle proof for each challenged target.

    Each proof corresponds to exactly one:
        (commitment_id, shard_index, chunk_index)
    """
    proofs = []

    for target in challenge_targets:
        (
            chunk_data,
            chunk_merkle_path,
            shard_merkle_path,
        ) = storage.get_chunk_response(
            chunk_id=target.commitment_id,
            shard_index=target.shard_index,
            chunk_index=target.chunk_index,
        )

        witness = ShardWitness(
            commitment_id=target.commitment_id,
            shard_index=target.shard_index,
            chunk_index=target.chunk_index,
            chunk_data=chunk_data,
            chunk_merkle_path=chunk_merkle_path,
            shard_merkle_path=shard_merkle_path,
        )

        proof = generate_shard_proof(
            target=target,
            p=p,
            witness=witness,
        )

        proofs.append(proof)

    return tuple(proofs)


def one_run(
    file_path: Path,
    payload: bytes,
    repeat: int,
    run_type: str,
    data_shards: int,
    total_shards: int,
    challenge_size: int,
) -> dict[str, Any]:
    """Execute one complete baseline-pipeline run and return its metrics."""
    run_id = uuid.uuid4().hex[:12]
    digest = hashlib.sha256(payload).hexdigest()
    end_to_end_start = time.perf_counter()

    with tempfile.TemporaryDirectory(prefix="engram_baseline_") as temp_dir:
        storage = StorageManager(
            Path(temp_dir) / "storage",
            node_count=total_shards,
        )

        # Generate a new symmetric key for this run and measure the time taken.
        key, key_generation_ms = measure_ms(generate_key)

        # Encrypt the payload and measure the time taken.
        encrypted, encryption_ms = measure_ms(encrypt, payload, key)

        # Erasure-encode the ciphertext and measure the time taken.
        encoded, erasure_encoding_ms = measure_ms(
            encode,
            encrypted.ciphertext,
            data_shards=data_shards,
            total_shards=total_shards,
        )

        # Start measuring the time taken for storage write and Merkle commitment generation.
        # 1. Generate Merkle commitments.
        commitment, commitment_build_ms = measure_ms(
            storage.build_commitment,
            list(encoded.shards),
        )

        # 2. Write encoded shard files.
        shard_records, shard_write_ms = measure_ms(
            storage.write_shards,
            "payload",
            list(encoded.shards),
        )

        # 3. Build manifest metadata, including SHA-256 hashes.
        manifest, manifest_build_ms = measure_ms(
            storage.build_manifest,
            "payload",
            encoded,
            list(encoded.shards),
            shard_records,
            commitment,
        )

        # 4. Persist the manifest.
        _, manifest_write_ms = measure_ms(
            storage.persist_manifest,
            "payload",
            manifest,
        )

        # Load the available shards and measure the time taken.
        available, storage_read_ms = measure_ms(
            storage.load_available_shards,
            "payload",
        )

        metadata = storage.get_chunk_metadata("payload")

        # Reconstruct the original ciphertext from the available shards and measure the time taken.
        restored_ciphertext, reconstruction_ms = measure_ms(
            decode,
            available,
            original_size=metadata.original_size,
            data_shards=data_shards,
            total_shards=total_shards,
        )

        # Decrypt the restored ciphertext and measure the time taken.
        restored_payload, decryption_ms = measure_ms(
            decrypt,
            EncryptedPayload(
                ciphertext=restored_ciphertext,
                nonce=encrypted.nonce,
            ),
            key,
        )
        reconstruction_success = restored_payload == payload

        chunk_counts = storage.get_chunk_counts("payload")
        # Generate a challenge and measure the time taken.
        challenge, challenge_generation_ms = measure_ms(
            create_challenge,
            chunk_id="payload",
            total_shards=total_shards,
            chunks_per_shard=chunk_counts,
            k=challenge_size,
            block_hash=digest,
            miner_id="engram-baseline-benchmark",
            epoch_id="fixed-baseline-epoch",
        )

        # ---------------------------------------------------------
        # Generate challenge
        # ---------------------------------------------------------

        chunk_counts = tuple(storage.get_chunk_counts("payload"))

        challenge, challenge_generation_ms = measure_ms(
            create_challenge,
            chunk_id="payload",
            total_shards=total_shards,
            chunks_per_shard=chunk_counts,
            k=challenge_size,
            block_hash=digest,
            miner_id="engram-baseline-benchmark",
            epoch_id="fixed-baseline-epoch",
        )

        # Convert challenge indices into explicit targets.
        challenge_targets = tuple(
            ShardChallenge(
                commitment_id="payload",
                shard_index=shard_index,
                chunk_index=chunk_index,
            )
            for shard_index, chunk_index in zip(
                challenge.shard_indices,
                challenge.chunk_indices,
            )
        )

        # ---------------------------------------------------------
        # Generate one Python Merkle proof per target
        # ---------------------------------------------------------

        storage_proofs, python_merkle_proof_generation_ms = measure_ms(
            generate_challenge_shard_proofs,
            storage=storage,
            challenge_targets=challenge_targets,
            p=challenge.p,
        )

        # ---------------------------------------------------------
        # Verify the entire challenge
        # ---------------------------------------------------------

        expected_root = storage.get_merkle_root("payload")

        verification_result, python_merkle_verification_ms = measure_ms(
            verify_challenge_proofs,
            challenge_targets=challenge_targets,
            proofs=storage_proofs,
            registered_roots={
                "payload": expected_root,
            },
            expected_p=challenge.p,
            layouts={
                "payload": chunk_counts,
            },
        )

        proof_success = verification_result.valid
        verified_proof_count = verification_result.verified_count
        failed_proof_count = len(verification_result.failed_targets)

        stored_payload_bytes = storage.get_stored_bytes()

        end_to_end_ms = (time.perf_counter() - end_to_end_start) * 1000.0

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
            "storage_overhead_ratio": (
                stored_payload_bytes / len(payload) if payload else None
            ),
            "available_shards": len(available),
            "chunks_per_shard": chunk_counts[0],
            "challenge_shard_indices": ";".join(map(str, challenge.shard_indices)),
            "challenge_chunk_indices": ";".join(map(str, challenge.chunk_indices)),
            "challenge_target_count": len(challenge_targets),
            "proof_count": len(storage_proofs),
            "verified_proof_count": verified_proof_count,
            "failed_proof_count": failed_proof_count,
            "key_generation_ms": key_generation_ms,
            "encryption_ms": encryption_ms,
            "erasure_encoding_ms": erasure_encoding_ms,
            "commitment_build_ms": commitment_build_ms,
            "shard_write_ms": shard_write_ms,
            "manifest_build_ms": manifest_build_ms,
            "manifest_write_ms": manifest_write_ms,
            "storage_total_ms": (
                commitment_build_ms
                + shard_write_ms
                + manifest_build_ms
                + manifest_write_ms
            ),
            "storage_read_ms": storage_read_ms,
            "reconstruction_ms": reconstruction_ms,
            "decryption_ms": decryption_ms,
            "challenge_generation_ms": challenge_generation_ms,
            "python_merkle_proof_generation_ms": (python_merkle_proof_generation_ms),
            "python_merkle_verification_ms": (python_merkle_verification_ms),
            "reconstruction_success": reconstruction_success,
            "python_merkle_proof_success": proof_success,
            "pipeline_end_to_end_ms": end_to_end_ms,
            "proof_scope": (
                "Python per-target two-level Merkle proofs; "
                "one proof per challenged chunk; not Groth16"
            ),
        }


def main() -> None:
    """CLI entry point for the baseline pipeline benchmark."""
    parser = argparse.ArgumentParser(
        description=("Benchmark the current ENGRAM Python storage pipeline baseline.")
    )
    parser.add_argument("files", nargs="+", help="Input files to benchmark")
    parser.add_argument(
        "--repeat",
        type=int,
        default=30,
        help="Measured runs per file (default: 30)",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=2,
        help="Warm-up runs per file (excluded from summary)",
    )
    parser.add_argument("--data-shards", type=int, default=4)
    parser.add_argument("--total-shards", type=int, default=8)
    parser.add_argument("--challenge-size", type=int, default=3)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("results/pipeline"),
    )
    args = parser.parse_args()

    if args.repeat < 1 or args.warmup < 0:
        parser.error("--repeat must be >= 1 and --warmup must be >= 0")

    if not (1 <= args.data_shards < args.total_shards <= 255):
        parser.error("Require 1 <= data-shards < total-shards <= 255")

    if not (1 <= args.challenge_size <= args.total_shards):
        parser.error("--challenge-size must be between 1 and total-shards")

    rows: list[dict[str, Any]] = []
    files_metadata: list[dict[str, Any]] = []

    for raw in args.files:
        path = Path(raw).expanduser().resolve()

        if not path.is_file():
            parser.error(f"Input file does not exist: {path}")

        payload = path.read_bytes()

        if not payload:
            parser.error(f"Input file is empty: {path}")

        digest = hashlib.sha256(payload).hexdigest()
        files_metadata.append(
            {
                "path": str(path),
                "size_bytes": len(payload),
                "sha256": digest,
            }
        )

        print(f"\n=== {path} ({len(payload)} bytes; sha256={digest}) ===")

        for i in range(1, args.warmup + 1):
            one_run(
                path,
                payload,
                i,
                "warmup",
                args.data_shards,
                args.total_shards,
                args.challenge_size,
            )
            print(
                f"[warmup {i}/{args.warmup}] completed " "(excluded from CSV/summary)"
            )

        for i in range(1, args.repeat + 1):
            row = one_run(
                path,
                payload,
                i,
                "measurement",
                args.data_shards,
                args.total_shards,
                args.challenge_size,
            )
            rows.append(row)

            print(
                f"[{i}/{args.repeat}] "
                f"e2e={row['pipeline_end_to_end_ms']:.2f} ms; "
                f"store+Merkle="
                f"{row['storage_total_ms']:.2f} ms; "
                f"Python proofs="
                f"{row['verified_proof_count']}/{row['proof_count']}"
            )

            if (
                not row["reconstruction_success"]
                or not row["python_merkle_proof_success"]
                or row["verified_proof_count"] != args.challenge_size
                or row["proof_count"] != args.challenge_size
            ):
                raise RuntimeError(
                    "Correctness check failed in measurement " f"run {i} for {path}"
                )

    summary = {
        "config": {
            "repeat": args.repeat,
            "warmup": args.warmup,
            "data_shards": args.data_shards,
            "total_shards": args.total_shards,
            "challenge_size": args.challenge_size,
            "timer": "time.perf_counter",
            "input_files": files_metadata,
            "excluded_from_timing": [
                "Python interpreter startup",
                "input file read before each run",
                "Groth16 compilation/setup/proof/verification",
            ],
        },
        "environment": {
            "python_version": sys.version,
            "platform": platform.platform(),
        },
        "metric_definitions": {
            "storage_total_ms": (
                "StorageManager.store_chunk: writes shard payloads and "
                "manifest, builds per-shard chunk Merkle trees and the "
                "global shard-root tree."
            ),
            "stored_payload_bytes": (
                "Sum of shard file payload sizes; excludes manifests, "
                "filesystem metadata, and physical disk overhead."
            ),
            "storage_overhead_ratio": (
                "stored_payload_bytes / original plaintext bytes."
            ),
            "python_merkle_proof_generation_ms": (
                "Time to retrieve each challenged chunk and its two Merkle paths "
                "from StorageManager, construct ShardWitness objects, and generate "
                "one Python ShardProof per challenge target. Not Groth16 proving."
            ),
            "python_merkle_verification_ms": (
                "Time to verify all per-target Python Merkle proofs against "
                "registered commitment roots and aggregate the challenge result. "
                "Not Groth16 verification."
            ),
            "pipeline_end_to_end_ms": (
                "Includes temporary directory and StorageManager "
                "initialization plus all measured pipeline stages."
            ),
        },
        "summary": summarize_pipeline_rows(rows),
    }

    exported = export_results(
        output_dir=args.output_dir.resolve(),
        prefix="pipeline_baseline",
        rows=rows,
        summary=summary,
        # Preserve the original baseline CSV column order.
        csv_fieldnames=list(rows[0].keys()),
    )

    print(f"\nRaw measurements: {exported.csv_path}" f"\nSummary: {exported.json_path}")


if __name__ == "__main__":
    main()
