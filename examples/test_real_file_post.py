"""Prepare real-file data and Circom input for the current two-level PoSt circuit.

Usage:
  PYTHONPATH=src python examples/test_real_file_post.py path/to/file

This prepares artifacts only; circuit compilation, Groth16 setup and repeated
measurements are separate steps.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engram.challenge import create_challenge
from engram.encryption import EncryptedPayload, decrypt, encrypt, generate_key
from engram.erasure_coding import decode, encode
from engram.merkle import CHUNK_BYTES
from engram.proof import generate_proof, verify_storage_proof
from engram.storage import StorageManager
from zk.generate_circuit import DEFAULT_OUTPUT, DEFAULT_TEMPLATE, generate_circuit
from zk.generate_post_input import make_post_input

ZK_DIR = ROOT / "zk"
INPUT_JSON = ZK_DIR / "input_real_file.json"
METADATA_JSON = ZK_DIR / "input_real_file_metadata.json"
DATA_SHARDS = 4
TOTAL_SHARDS = 8
CHALLENGE_SIZE = 3


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: PYTHONPATH=src python examples/test_real_file_post.py <file_path>")
    file_path = Path(sys.argv[1]).expanduser().resolve()
    if not file_path.is_file():
        raise FileNotFoundError(f"Input file not found: {file_path}")
    original = file_path.read_bytes()
    if not original:
        raise ValueError("Input file must not be empty")

    print(f"Input file: {file_path}")
    print(f"Original size: {len(original)} bytes")

    key = generate_key()
    encrypted = encrypt(original, key)
    encoded = encode(encrypted.ciphertext, data_shards=DATA_SHARDS, total_shards=TOTAL_SHARDS)
    shards = list(encoded.shards)
    shard_size = len(shards[0])
    if shard_size <= CHUNK_BYTES:
        raise ValueError(
            f"Shard size {shard_size} bytes is too small for the current circuit; "
            f"each shard must contain at least two {CHUNK_BYTES}-byte chunks."
        )

    # Validate that the simulation can store, retrieve, reconstruct and decrypt.
    with tempfile.TemporaryDirectory(prefix="engram_real_file_post_") as temp_dir:
        storage = StorageManager(Path(temp_dir) / "storage", node_count=TOTAL_SHARDS)
        storage.store_chunk("real_file", encoded)
        available = storage.load_available_shards("real_file")
        metadata = storage.get_chunk_metadata("real_file")
        recovered_ciphertext = decode(
            available, original_size=metadata.original_size,
            data_shards=DATA_SHARDS, total_shards=TOTAL_SHARDS,
        )
        recovered = decrypt(EncryptedPayload(recovered_ciphertext, encrypted.nonce), key)
        if recovered != original:
            raise RuntimeError("Reconstructed plaintext differs from the original input")
        print("[PASS] Storage readback, RS reconstruction and decryption")

        digest = hashlib.sha256(original).hexdigest()
        chunk_counts = tuple((len(shard) + CHUNK_BYTES - 1) // CHUNK_BYTES for shard in shards)
        challenge = create_challenge(
            chunk_id="real_file", total_shards=TOTAL_SHARDS,
            chunks_per_shard=chunk_counts, k=CHALLENGE_SIZE,
            block_hash=digest, miner_id="engram-real-file", epoch_id="baseline-epoch",
        )
        post_input = make_post_input(shards, list(challenge.shard_indices), list(challenge.chunk_indices))
        # Check the same challenge and Merkle commitments using the Python verifier.
        proof = generate_proof(storage, "real_file", challenge.p, TOTAL_SHARDS, CHALLENGE_SIZE)
        if not verify_storage_proof(
            proof, storage.get_merkle_root("real_file"), challenge.p,
            TOTAL_SHARDS, CHALLENGE_SIZE, chunk_counts,
        ):
            raise RuntimeError("Python Merkle PoSt verification failed")
        print("[PASS] Python two-level Merkle proof verification")

    INPUT_JSON.write_text(json.dumps(post_input, indent=2), encoding="utf-8")
    n_chunks = chunk_counts[0]
    metadata_out = {
        "sourceFile": str(file_path),
        "sourceSizeBytes": len(original),
        "sourceSha256": digest,
        "ciphertextSizeBytes": len(encrypted.ciphertext),
        "dataShards": DATA_SHARDS,
        "nShards": TOTAL_SHARDS,
        "shardBytesLen": shard_size,
        "nChunksPerShard": n_chunks,
        "sampledShardCount": CHALLENGE_SIZE,
        "challengeShardIndices": list(challenge.shard_indices),
        "challengeChunkIndices": list(challenge.chunk_indices),
        "merkleRoot": post_input["merkleRoot"],
        "sourceType": "real_file",
    }
    METADATA_JSON.write_text(json.dumps(metadata_out, indent=2), encoding="utf-8")
    params = generate_circuit(DEFAULT_TEMPLATE, DEFAULT_OUTPUT, shard_size, TOTAL_SHARDS)

    print(f"Ciphertext size: {len(encrypted.ciphertext)} bytes")
    print(f"Shard size: {shard_size} bytes; chunks per shard: {n_chunks}")
    print(f"Challenge shards: {list(challenge.shard_indices)}")
    print(f"Challenge chunks: {list(challenge.chunk_indices)}")
    print(f"Generated input: {INPUT_JSON}")
    print(f"Generated metadata: {METADATA_JSON}")
    print(f"Generated circuit: {DEFAULT_OUTPUT}")
    print(f"Circuit parameters: {params}")


if __name__ == "__main__":
    main()
