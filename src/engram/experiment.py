"""Repeatable benchmarks for the ENGRAM-iARCHIVE storage simulation.

Run from the repository root:
    PYTHONPATH=src python -m engram.experiment test_data/simple.txt --repeat 30 --warmup 1

This script measures the Python/Merkle simulation only. It does NOT measure
Groth16 proof generation or verification; benchmark the Circom/snarkjs circuit
separately and label those results explicitly.
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
from engram.merkle import build_merkle_tree, verify_proof
from engram.storage import StorageManager


CI_BOOTSTRAP_SAMPLES = 2000


def _elapsed_ms(start: float) -> float:
    """Return elapsed wall-clock time in milliseconds using a monotonic clock."""
    return (time.perf_counter() - start) * 1000.0


def _store_payload(
    storage: StorageManager,
    chunk_id: str,
    payload: bytes,
    data_shards: int,
    total_shards: int,
) -> dict[str, Any]:
    """Encrypt, erasure-code, build a Merkle tree, and store one payload."""
    result: dict[str, Any] = {}

    start = time.perf_counter()
    key = generate_key()
    result["key_generation_ms"] = _elapsed_ms(start)

    start = time.perf_counter()
    encrypted = encrypt(payload, key)
    result["encryption_ms"] = _elapsed_ms(start)

    start = time.perf_counter()
    encoded = encode(
        encrypted.ciphertext,
        data_shards=data_shards,
        total_shards=total_shards,
    )
    result["erasure_encoding_ms"] = _elapsed_ms(start)

    start = time.perf_counter()
    tree = build_merkle_tree(list(encoded.shards))
    result["merkle_build_ms"] = _elapsed_ms(start)

    start = time.perf_counter()
    metadata = storage.store_chunk(
        chunk_id,
        encoded,
        precomputed_tree=tree,
    )
    result["storage_write_ms"] = _elapsed_ms(start)

    result.update({
        "_key": key,
        "_encrypted": encrypted,
        "_encoded": encoded,
        "_tree": tree,
        "_metadata": metadata,
        "ciphertext_size_bytes": len(encrypted.ciphertext),
        "shard_size_bytes": len(encoded.shards[0]),
        "total_shard_bytes": sum(map(len, encoded.shards)),
        "merkle_root": str(tree.root),
    })
    return result


def _verify_stored_payload(
    storage: StorageManager,
    chunk_id: str,
    key: bytes,
    encrypted: EncryptedPayload,
    original: bytes,
    data_shards: int,
    total_shards: int,
) -> tuple[float, float, float, bool, int]:
    """Measure shard read, RS reconstruction, and decryption independently."""
    start = time.perf_counter()
    available = storage.load_available_shards(chunk_id)
    storage_read_ms = _elapsed_ms(start)

    metadata = storage.get_chunk_metadata(chunk_id)
    start = time.perf_counter()
    restored_ciphertext = decode(
        available,
        original_size=metadata.original_size,
        data_shards=data_shards,
        total_shards=total_shards,
    )
    reconstruction_ms = _elapsed_ms(start)

    start = time.perf_counter()
    restored = decrypt(
        EncryptedPayload(
            ciphertext=restored_ciphertext,
            nonce=encrypted.nonce,
        ),
        key,
    )
    decryption_ms = _elapsed_ms(start)
    return (
        storage_read_ms,
        reconstruction_ms,
        decryption_ms,
        restored == original,
        len(available),
    )


def _base_pipeline_run(
    file_path: Path,
    original: bytes,
    repeat_index: int,
    data_shards: int,
    total_shards: int,
    challenge_size: int,
    run_type: str = "measurement",
) -> dict[str, Any]:
    """Measure one complete write/read/restore/Merkle-verification pipeline.

    pipeline_end_to_end_ms includes temporary-directory creation and storage
    initialization, as well as writing, reading, reconstruction, decryption,
    challenge generation, Merkle-path generation, and Merkle verification.
    """
    run_id = uuid.uuid4().hex[:12]
    digest = hashlib.sha256(original).hexdigest()
    wall_start = time.perf_counter()

    with tempfile.TemporaryDirectory(prefix="engram_bench_") as temp_dir:
        storage = StorageManager(Path(temp_dir) / "storage", node_count=total_shards)
        staged = _store_payload(
            storage, "payload", original, data_shards, total_shards
        )

        (
            storage_read_ms,
            reconstruction_ms,
            decryption_ms,
            reconstruction_success,
            available_count,
        ) = _verify_stored_payload(
            storage,
            "payload",
            staged["_key"],
            staged["_encrypted"],
            original,
            data_shards,
            total_shards,
        )

        challenge_start = time.perf_counter()
        challenge = create_challenge(
            chunk_id="payload",
            total_shards=total_shards,
            k=challenge_size,
            block_hash=digest,
            miner_id="engram-benchmark",
            epoch_id="fixed-benchmark-epoch",
        )
        challenge_ms = _elapsed_ms(challenge_start)

        path_start = time.perf_counter()
        proofs = [
            staged["_tree"].generate_proof(index)
            for index in challenge.shard_indices
        ]
        merkle_path_generation_ms = _elapsed_ms(path_start)

        available = storage.load_available_shards("payload")
        verify_start = time.perf_counter()
        proof_success = all(
            index in available
            and verify_proof(
                available[index],
                proof,
                staged["_tree"].root,
            )
            for index, proof in zip(challenge.shard_indices, proofs)
        )
        merkle_verification_ms = _elapsed_ms(verify_start)

        stored_bytes = storage.get_stored_bytes()
        pipeline_end_to_end_ms = _elapsed_ms(wall_start)

        return {
            "run_id": run_id,
            "run_type": run_type,
            "repeat": repeat_index,
            "scenario": "full_pipeline",
            "source_file": str(file_path),
            "input_sha256": digest,
            "input_size_bytes": len(original),
            "original_size_bytes": len(original),  # retained as a compatibility alias
            "ciphertext_size_bytes": staged["ciphertext_size_bytes"],
            "data_shards": data_shards,
            "total_shards": total_shards,
            "shard_size_bytes": staged["shard_size_bytes"],
            "total_shard_bytes": staged["total_shard_bytes"],
            "stored_payload_bytes": stored_bytes,
            # Ratio compares shard payload bytes with original plaintext bytes;
            # it does not include filesystem/manifest metadata or disk overhead.
            "storage_overhead_ratio": stored_bytes / len(original) if original else None,
            "merkle_root": staged["merkle_root"],
            "challenge_indices": ",".join(map(str, challenge.shard_indices)),
            "challenge_generation_ms": challenge_ms,
            "key_generation_ms": staged["key_generation_ms"],
            "encryption_ms": staged["encryption_ms"],
            "erasure_encoding_ms": staged["erasure_encoding_ms"],
            "storage_write_ms": staged["storage_write_ms"],
            "merkle_build_ms": staged["merkle_build_ms"],
            "storage_read_ms": storage_read_ms,
            "reconstruction_ms": reconstruction_ms,
            "decryption_ms": decryption_ms,
            # This is Merkle path generation, not a Groth16 circuit witness.
            "merkle_path_generation_ms": merkle_path_generation_ms,
            # This is Python Merkle verification, not Groth16 verification.
            "merkle_verification_ms": merkle_verification_ms,
            "available_shards": available_count,
            "reconstruction_success": reconstruction_success,
            "proof_success": proof_success,
            "proof_kind": "python_merkle_simulation_not_groth16",
            "pipeline_end_to_end_ms": pipeline_end_to_end_ms,
        }


def _incremental_append_run(
    file_path: Path,
    original: bytes,
    delta: bytes,
    repeat_index: int,
    data_shards: int,
    total_shards: int,
    run_type: str = "measurement",
) -> list[dict[str, Any]]:
    """Compare full-snapshot rewrite with independently encrypted append segments.

    This models append-only segments, not arbitrary in-place updates. The full
    rewrite replaces the previous snapshot; the incremental scenario retains
    the original segment and stores the appended bytes as a separate segment.
    """
    rows: list[dict[str, Any]] = []
    updated = original + delta
    digest = hashlib.sha256(original).hexdigest()
    pair_id = uuid.uuid4().hex[:12]

    for scenario in ("full_rewrite_update", "incremental_append_update"):
        with tempfile.TemporaryDirectory(prefix=f"engram_{scenario}_") as temp_dir:
            storage = StorageManager(Path(temp_dir) / "storage", node_count=total_shards)
            base = _store_payload(
                storage, "base", original, data_shards, total_shards
            )

            if scenario == "full_rewrite_update":
                update_start = time.perf_counter()

                start = time.perf_counter()
                new_key = generate_key()
                key_generation_ms = _elapsed_ms(start)

                start = time.perf_counter()
                new_encrypted = encrypt(updated, new_key)
                encryption_ms = _elapsed_ms(start)

                start = time.perf_counter()
                new_encoded = encode(
                    new_encrypted.ciphertext,
                    data_shards=data_shards,
                    total_shards=total_shards,
                )
                erasure_encoding_ms = _elapsed_ms(start)

                start = time.perf_counter()
                new_tree = build_merkle_tree(list(new_encoded.shards))
                merkle_build_ms = _elapsed_ms(start)

                start = time.perf_counter()
                deleted_bytes = storage.delete_chunk("base")
                storage_delete_ms = _elapsed_ms(start)

                start = time.perf_counter()
                storage.store_chunk(
                    "updated", new_encoded, precomputed_tree=new_tree
                )
                storage_write_ms = _elapsed_ms(start)
                update_processing_ms = _elapsed_ms(update_start)
                update_bytes_written = sum(map(len, new_encoded.shards))
                bytes_reencoded = len(updated)

                validation_start = time.perf_counter()
                available = storage.load_available_shards("updated")
                metadata = storage.get_chunk_metadata("updated")
                restored_ciphertext = decode(
                    available,
                    original_size=metadata.original_size,
                    data_shards=data_shards,
                    total_shards=total_shards,
                )
                restored = decrypt(
                    EncryptedPayload(
                        ciphertext=restored_ciphertext,
                        nonce=new_encrypted.nonce,
                    ),
                    new_key,
                )
                update_validation_ms = _elapsed_ms(validation_start)
                reconstruction_success = restored == updated
                total_stored = storage.get_stored_bytes()
                incremental_segment_bytes = 0

            else:
                update_start = time.perf_counter()

                start = time.perf_counter()
                delta_key = generate_key()
                key_generation_ms = _elapsed_ms(start)

                start = time.perf_counter()
                delta_encrypted = encrypt(delta, delta_key)
                encryption_ms = _elapsed_ms(start)

                start = time.perf_counter()
                delta_encoded = encode(
                    delta_encrypted.ciphertext,
                    data_shards=data_shards,
                    total_shards=total_shards,
                )
                erasure_encoding_ms = _elapsed_ms(start)

                start = time.perf_counter()
                delta_tree = build_merkle_tree(list(delta_encoded.shards))
                merkle_build_ms = _elapsed_ms(start)

                start = time.perf_counter()
                storage.store_chunk("delta", delta_encoded, precomputed_tree=delta_tree)
                storage_write_ms = _elapsed_ms(start)
                storage_delete_ms = 0.0
                update_processing_ms = _elapsed_ms(update_start)
                update_bytes_written = sum(map(len, delta_encoded.shards))
                bytes_reencoded = len(delta)
                deleted_bytes = 0

                # Validation/reconstruction is intentionally timed separately
                # from update processing, so the update latency is comparable.
                validation_start = time.perf_counter()
                base_available = storage.load_available_shards("base")
                base_ciphertext = decode(
                    base_available,
                    original_size=base["_metadata"].original_size,
                    data_shards=data_shards,
                    total_shards=total_shards,
                )
                base_plaintext = decrypt(
                    EncryptedPayload(
                        ciphertext=base_ciphertext,
                        nonce=base["_encrypted"].nonce,
                    ),
                    base["_key"],
                )

                delta_available = storage.load_available_shards("delta")
                delta_metadata = storage.get_chunk_metadata("delta")
                delta_ciphertext = decode(
                    delta_available,
                    original_size=delta_metadata.original_size,
                    data_shards=data_shards,
                    total_shards=total_shards,
                )
                delta_plaintext = decrypt(
                    EncryptedPayload(
                        ciphertext=delta_ciphertext,
                        nonce=delta_encrypted.nonce,
                    ),
                    delta_key,
                )
                reconstruction_success = base_plaintext + delta_plaintext == updated
                update_validation_ms = _elapsed_ms(validation_start)
                total_stored = storage.get_stored_bytes()
                incremental_segment_bytes = sum(map(len, delta_encoded.shards))

            rows.append({
                "run_id": uuid.uuid4().hex[:12],
                "update_pair_id": pair_id,
                "run_type": run_type,
                "repeat": repeat_index,
                "scenario": scenario,
                "source_file": str(file_path),
                "input_sha256": digest,
                "base_size_bytes": len(original),
                "delta_size_bytes": len(delta),
                "updated_size_bytes": len(updated),
                "data_shards": data_shards,
                "total_shards": total_shards,
                "key_generation_ms": key_generation_ms,
                "encryption_ms": encryption_ms,
                "erasure_encoding_ms": erasure_encoding_ms,
                "merkle_build_ms": merkle_build_ms,
                "storage_delete_ms": storage_delete_ms,
                "storage_write_ms": storage_write_ms,
                "update_processing_ms": update_processing_ms,
                "update_validation_ms": update_validation_ms,
                # Counts shard payload bytes written, not physical disk I/O.
                "update_bytes_written": update_bytes_written,
                # Plaintext bytes passed through the encrypt/encode update path.
                "bytes_reencoded": bytes_reencoded,
                "deleted_bytes": deleted_bytes,
                "total_stored_bytes_after_update": total_stored,
                "incremental_segment_bytes": incremental_segment_bytes,
                "write_amplification": (
                    update_bytes_written / len(delta) if len(delta) else None
                ),
                "storage_overhead_ratio": (
                    total_stored / len(updated) if len(updated) else None
                ),
                "reconstruction_success": reconstruction_success,
                "update_success": reconstruction_success,
                "update_model": "append_only_independent_encrypted_segments",
            })

    # Pairwise derived comparisons use the same repeat, input, delta, and RS config.
    full = next(r for r in rows if r["scenario"] == "full_rewrite_update")
    incremental = next(r for r in rows if r["scenario"] == "incremental_append_update")
    full_time = full["update_processing_ms"]
    inc_time = incremental["update_processing_ms"]
    full_written = full["update_bytes_written"]
    inc_written = incremental["update_bytes_written"]
    for row in rows:
        row["update_time_saved_percent"] = (
            (full_time - inc_time) / full_time * 100.0 if full_time else None
        )
        row["update_bytes_written_saved_percent"] = (
            (full_written - inc_written) / full_written * 100.0
            if full_written else None
        )
    return rows


def _percentile(values: list[float], percentile: float) -> float:
    """Nearest-rank percentile, with percentile in the inclusive range [0, 100]."""
    ordered = sorted(values)
    if not ordered:
        raise ValueError("percentile requires at least one value")
    index = max(0, min(len(ordered) - 1, int((percentile / 100.0) * len(ordered) + 0.999999) - 1))
    return ordered[index]


def _median_bootstrap_ci(
    values: list[float], seed: int, samples: int = CI_BOOTSTRAP_SAMPLES
) -> tuple[float, float]:
    """Approximate percentile-bootstrap 95% CI for the median."""
    if not values:
        raise ValueError("CI requires at least one value")
    if len(values) == 1:
        return values[0], values[0]
    rng = random.Random(seed)
    medians = [
        statistics.median(rng.choices(values, k=len(values)))
        for _ in range(samples)
    ]
    return _percentile(medians, 2.5), _percentile(medians, 97.5)


def _write_results(
    rows: list[dict[str, Any]],
    output_dir: Path,
    config: dict[str, Any],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    # Unique filenames prevent later configurations/runs from silently overwriting data.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = uuid.uuid4().hex[:6]
    stem = f"benchmark_{stamp}_{suffix}"
    csv_path = output_dir / f"{stem}_runs.csv"
    json_path = output_dir / f"{stem}_summary.json"

    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)

    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(
            (row["source_file"], row["scenario"], row["run_type"]), []
        ).append(row)

    summary: list[dict[str, Any]] = []
    for group_index, ((source_file, scenario, run_type), group) in enumerate(groups.items()):
        item: dict[str, Any] = {
            "source_file": source_file,
            "scenario": scenario,
            "run_type": run_type,
            "runs": len(group),
        }
        numeric_keys = sorted({
            key for row in group for key, value in row.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
            and key not in {"repeat"}
        })
        for metric_index, key in enumerate(numeric_keys):
            values = [
                float(row[key]) for row in group
                if isinstance(row.get(key), (int, float))
                and not isinstance(row.get(key), bool)
            ]
            if values:
                item[f"{key}_median"] = statistics.median(values)
                item[f"{key}_p95"] = _percentile(values, 95)
                item[f"{key}_min"] = min(values)
                item[f"{key}_max"] = max(values)
                ci_low, ci_high = _median_bootstrap_ci(
                    values, seed=20261022 + group_index * 1000 + metric_index
                )
                item[f"{key}_median_ci95_low"] = ci_low
                item[f"{key}_median_ci95_high"] = ci_high

        for bool_key in ("reconstruction_success", "proof_success", "update_success"):
            if any(bool_key in row for row in group):
                item[f"{bool_key}_all_passed"] = all(
                    row.get(bool_key) is True for row in group
                )
        summary.append(item)

    metadata = {
        "config": config,
        "environment": {
            "python_version": sys.version,
            "platform": platform.platform(),
        },
        "metric_definitions": {
            "pipeline_end_to_end_ms": (
                "Includes temp directory and StorageManager setup, then full write, "
                "read, reconstruction, decryption, challenge, Merkle paths and verification."
            ),
            "update_processing_ms": (
                "Update-path key generation, encryption, RS encoding, Merkle build, "
                "and storage mutation/write; excludes post-update validation."
            ),
            "update_validation_ms": (
                "Time to read/reconstruct/decrypt and validate the updated logical payload."
            ),
            "update_bytes_written": (
                "Sum of encoded shard payload bytes written by the update; excludes filesystem "
                "metadata and does not claim to measure physical disk writes."
            ),
            "bytes_reencoded": (
                "Plaintext bytes passed through the update encryption/encoding path: full "
                "updated object for full rewrite, appended delta only for incremental append."
            ),
            "storage_overhead_ratio": (
                "Stored shard payload bytes divided by original/updated plaintext size; "
                "does not include manifest, filesystem metadata, or physical storage overhead."
            ),
            "merkle_path_generation_ms": (
                "Python Merkle path generation; not Groth16 circuit witness generation."
            ),
            "merkle_verification_ms": (
                "Python Merkle proof verification; not Groth16 proof verification."
            ),
            "write_amplification": (
                "update_bytes_written / delta_size_bytes; describes shard payload write volume "
                "relative to appended plaintext size."
            ),
            "update_time_saved_percent": (
                "100 * (full rewrite update time - incremental update time) / full rewrite time. "
                "The same paired value is attached to both rows in a pair."
            ),
        },
        "summary": summary,
    }
    json_path.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\nRaw measurements: {csv_path}")
    print(f"Summary + definitions: {json_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark ENGRAM encryption, RS encoding, storage, Merkle simulation, "
            "and full-rewrite versus append-only updates."
        )
    )
    parser.add_argument("files", nargs="+", help="Input files to benchmark")
    parser.add_argument("--repeat", type=int, default=3, help="Measured runs per scenario (default: 3; smoke test only)")
    parser.add_argument("--warmup", type=int, default=1, help="Warm-up runs per file, excluded from measurements (default: 1)")
    parser.add_argument("--data-shards", type=int, default=4)
    parser.add_argument("--total-shards", type=int, default=8)
    parser.add_argument("--challenge-size", type=int, default=3)
    parser.add_argument(
        "--append-bytes",
        type=int,
        default=1024,
        help="Synthetic appended delta size in bytes (default: 1024)",
    )
    parser.add_argument("--skip-update", action="store_true", help="Run only the full pipeline")
    parser.add_argument("--output-dir", type=Path, default=Path("results"), help="Directory for uniquely named CSV/JSON outputs")
    args = parser.parse_args()

    if args.repeat < 1:
        parser.error("--repeat must be at least 1")
    if args.warmup < 0:
        parser.error("--warmup cannot be negative")
    if not (1 <= args.data_shards < args.total_shards <= 255):
        parser.error("Require 1 <= data-shards < total-shards <= 255")
    if not (1 <= args.challenge_size <= args.total_shards):
        parser.error("--challenge-size must be between 1 and total-shards")
    if args.append_bytes < 0:
        parser.error("--append-bytes cannot be negative")

    all_rows: list[dict[str, Any]] = []
    config = {
        "repeat": args.repeat,
        "warmup": args.warmup,
        "data_shards": args.data_shards,
        "total_shards": args.total_shards,
        "challenge_size": args.challenge_size,
        "append_bytes": args.append_bytes,
        "skip_update": args.skip_update,
        "timer": "time.perf_counter",
        "proof_scope": "Python Merkle simulation; Groth16 measured separately",
    }

    for raw_path in args.files:
        file_path = Path(raw_path).expanduser().resolve()
        if not file_path.is_file():
            parser.error(f"Input file does not exist: {file_path}")
        original = file_path.read_bytes()
        if not original:
            parser.error(f"Input file is empty: {file_path}")

        delta = bytes(((i * 131 + 17) % 256) for i in range(args.append_bytes))
        print(f"\n=== Benchmark: {file_path} ({len(original)} bytes) ===")

        for warmup_index in range(1, args.warmup + 1):
            _base_pipeline_run(
                file_path, original, warmup_index, args.data_shards,
                args.total_shards, args.challenge_size, run_type="warmup",
            )
            if not args.skip_update:
                _incremental_append_run(
                    file_path, original, delta, warmup_index,
                    args.data_shards, args.total_shards, run_type="warmup",
                )
            print(f"[warmup {warmup_index}/{args.warmup}] completed (excluded from results)")

        for repeat_index in range(1, args.repeat + 1):
            row = _base_pipeline_run(
                file_path,
                original,
                repeat_index,
                args.data_shards,
                args.total_shards,
                args.challenge_size,
                run_type="measurement",
            )
            all_rows.append(row)
            print(
                f"[{repeat_index}/{args.repeat}] full_pipeline "
                f"{row['pipeline_end_to_end_ms']:.2f} ms; "
                f"stored={row['stored_payload_bytes']} bytes; "
                f"reconstruction={row['reconstruction_success']}; "
                f"Merkle proof={row['proof_success']}"
            )

            if not args.skip_update:
                update_rows = _incremental_append_run(
                    file_path,
                    original,
                    delta,
                    repeat_index,
                    args.data_shards,
                    args.total_shards,
                    run_type="measurement",
                )
                all_rows.extend(update_rows)
                for update_row in update_rows:
                    print(
                        f"[{repeat_index}/{args.repeat}] {update_row['scenario']} "
                        f"{update_row['update_processing_ms']:.2f} ms; "
                        f"written={update_row['update_bytes_written']} bytes; "
                        f"re-encoded={update_row['bytes_reencoded']} bytes; "
                        f"valid={update_row['update_success']}"
                    )

    _write_results(all_rows, args.output_dir, config)


if __name__ == "__main__":
    main()
