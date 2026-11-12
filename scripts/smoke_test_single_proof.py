"""Prepare a deterministic workload and one single-chunk PoSt input.

Usage:
    PYTHONPATH=src python scripts/smoke_test_single_proof.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks.common.workload import generate_update_workload
from engram.erasure_coding import encode, decode
from engram.merkle import CHUNK_BYTES
from engram.storage import StorageManager

from zk.input_builder import build_and_save_single_chunk_input
from zk.generate_circuit import (
    DEFAULT_TEMPLATE,
    DEFAULT_OUTPUT,
    generate_circuit,
)

# Workload configuration
INITIAL_SIZE = 64 * 1024  # 64 KiB
SEED = 20261029

# Reed-Solomon configuration
DATA_SHARDS = 4
TOTAL_SHARDS = 8

# One proof target
CHUNK_ID = "smoke_single_proof"
SHARD_INDEX = 2

# Output files
OUTPUT_DIR = ROOT / "zk" / "build" / "smoke_single"
INPUT_JSON = OUTPUT_DIR / "input.json"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Generate deterministic workload.
    workload = generate_update_workload(
        initial_size=INITIAL_SIZE,
        delta_size=1,  # Must be positive, even when num_deltas=0.
        num_deltas=0,
        seed=SEED,
    )
    payload = workload.initial_data

    print("=== Workload ===")
    print(f"Payload size: {len(payload)} bytes")
    print(f"Seed: {SEED}")

    # 2. Reed-Solomon encode.
    encoded = encode(
        payload,
        data_shards=DATA_SHARDS,
        total_shards=TOTAL_SHARDS,
    )
    shard_size = len(encoded.shards[0])
    n_chunks = (shard_size + CHUNK_BYTES - 1) // CHUNK_BYTES

    if shard_size <= CHUNK_BYTES:
        raise ValueError("Shard must contain at least two chunks for this circuit")

    if not 0 <= SHARD_INDEX < TOTAL_SHARDS:
        raise ValueError("Invalid SHARD_INDEX")

    # Use chunk 1 to exercise a non-zero Merkle leaf index.
    chunk_index = min(1, n_chunks - 1)

    print("\n=== Reed-Solomon ===")
    print(f"Data shards: {DATA_SHARDS}")
    print(f"Total shards: {TOTAL_SHARDS}")
    print(f"Shard size: {shard_size} bytes")
    print(f"Chunks per shard: {n_chunks}")

    # 3. Store shards, read them back and verify reconstruction.
    # A temporary directory avoids reusing stale manifests from previous runs.
    with tempfile.TemporaryDirectory(prefix="engram_single_proof_") as temp_dir:
        storage = StorageManager(
            Path(temp_dir) / "storage",
            node_count=TOTAL_SHARDS,
        )

        storage.store_chunk(CHUNK_ID, encoded)

        available = storage.load_available_shards(CHUNK_ID)
        stored_metadata = storage.get_chunk_metadata(CHUNK_ID)

        recovered = decode(
            available,
            original_size=stored_metadata.original_size,
            data_shards=DATA_SHARDS,
            total_shards=TOTAL_SHARDS,
        )

        if recovered != payload:
            raise RuntimeError("Reconstructed payload differs from original workload")

        print("\n[PASS] Storage readback and RS reconstruction")

        # 4. Build one circuit input using the stored Merkle paths.
        input_path = build_and_save_single_chunk_input(
            storage=storage,
            chunk_id=CHUNK_ID,
            shard_index=SHARD_INDEX,
            chunk_index=chunk_index,
            output_path=INPUT_JSON,
        )

        # 5. Generate circuit using the metadata sidecar.
        params = generate_circuit(
            DEFAULT_TEMPLATE,
            DEFAULT_OUTPUT,
            shard_size,
            TOTAL_SHARDS,
        )

    metadata_path = INPUT_JSON.with_name(f"{INPUT_JSON.stem}_metadata.json")

    print("\n[PASS] Single-chunk input generated")
    print(f"Chunk ID: {CHUNK_ID}")
    print(f"Shard index: {SHARD_INDEX}")
    print(f"Chunk index: {chunk_index}")
    print(f"Input: {input_path}")
    print(f"Metadata: {metadata_path}")
    print(f"Circuit: {DEFAULT_OUTPUT}")
    print(f"Circuit parameters: {params}")

    # The shell script reads this line to find the generated circuit.
    print(f"CIRCUIT_OUTPUT={Path(DEFAULT_OUTPUT).resolve()}")


if __name__ == "__main__":
    main()
