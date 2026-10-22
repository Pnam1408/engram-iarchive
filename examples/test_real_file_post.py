import atexit
import json
import random
import re
import sys
import tempfile
from pathlib import Path

from engram.encryption import generate_key, encrypt, decrypt, EncryptedPayload
from engram.erasure_coding import encode, decode
from engram.merkle import (
    build_chunk_merkle_tree,
    build_hash_tree,
    encode_shard,
    hash_chunk,
    verify_hash_path,
)
from engram.storage import StorageManager

ROOT = Path(__file__).resolve().parents[1]
ZK_DIR = ROOT / "zk"
CIRCUIT_TEMPLATE = ZK_DIR / "circuits" / "engram_post.circom"
CIRCUIT_GENERATED = ZK_DIR / "circuits" / "engram_post_real_file.circom"
INPUT_JSON = ZK_DIR / "input_real_file.json"
METADATA_JSON = ZK_DIR / "test_real_file_metadata.json"

DATA_SHARDS = 4
TOTAL_SHARDS = 8
CHALLENGE_SIZE = 3
CHUNK_BYTES = 31
SHARD_TREE_DEPTH = 3


def check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(f"[FAIL] {message}")
    print(f"[PASS] {message}")


def make_post_input(shards: list[bytes], indices: list[int]) -> tuple[dict, dict]:
    """Build a chunk-level PoSt witness for 3 selected shards."""
    if len(shards) != TOTAL_SHARDS:
        raise ValueError(f"This circuit expects exactly {TOTAL_SHARDS} shards")
    if len(indices) != CHALLENGE_SIZE or len(set(indices)) != CHALLENGE_SIZE:
        raise ValueError(f"Provide {CHALLENGE_SIZE} distinct shard indices")
    if any(not 0 <= i < len(shards) for i in indices):
        raise ValueError("Shard index out of range")
    if len({len(shard) for shard in shards}) != 1:
        raise ValueError("All shards must have the same length")

    chunk_trees = [build_chunk_merkle_tree(shard) for shard in shards]
    outer_tree = build_hash_tree([tree.root for tree in chunk_trees])

    chunk_indices = [
        random.SystemRandom().randrange(chunk_trees[shard_index].leaf_count)
        for shard_index in indices
    ]

    chunk_values = []
    chunk_lengths = []
    chunk_paths = []
    shard_paths = []

    for shard_index, chunk_index in zip(indices, chunk_indices):
        shard = shards[shard_index]
        start = chunk_index * CHUNK_BYTES
        chunk = shard[start:start + CHUNK_BYTES]
        chunk_values.append(str(int.from_bytes(chunk, "big")))
        chunk_lengths.append(len(chunk))

        chunk_proof = chunk_trees[shard_index].generate_proof(chunk_index)
        chunk_paths.append([
            str(sibling_hash) for sibling_hash, _ in chunk_proof.siblings
        ])

        shard_proof = outer_tree.generate_proof(shard_index)
        shard_paths.append([
            str(sibling_hash) for sibling_hash, _ in shard_proof.siblings
        ])

        check(
            verify_hash_path(
                hash_chunk(chunk), chunk_proof, chunk_trees[shard_index].root
            ),
            f"Python xác minh chunk Merkle proof cho shard {shard_index}, chunk {chunk_index}",
        )
        check(
            verify_hash_path(
                chunk_trees[shard_index].root, shard_proof, outer_tree.root
            ),
            f"Python xác minh shard Merkle proof cho shard {shard_index}",
        )

    post_input = {
        "merkleRoot": str(outer_tree.root),
        "idx0": str(indices[0]),
        "idx1": str(indices[1]),
        "idx2": str(indices[2]),
        "chunkIdx0": str(chunk_indices[0]),
        "chunkIdx1": str(chunk_indices[1]),
        "chunkIdx2": str(chunk_indices[2]),
        "chunkValues": chunk_values,
        "chunkLengths": [str(v) for v in chunk_lengths],
        "chunkPaths": chunk_paths,
        "shardPaths": shard_paths,
    }

    metadata = {
        "chunk_trees": chunk_trees,
        "outer_tree": outer_tree,
        "chunk_indices": chunk_indices,
        "n_chunks_per_shard": chunk_trees[0].leaf_count,
    }
    return post_input, metadata


def generate_circuit(shard_bytes_len: int) -> tuple[int, int]:
    """Generate a circuit sized for this shard's chunk count and Merkle depth."""
    n_chunks = (shard_bytes_len + CHUNK_BYTES - 1) // CHUNK_BYTES
    chunk_depth = max(1, (n_chunks - 1).bit_length())
    source = CIRCUIT_TEMPLATE.read_text(encoding="utf-8")

    pattern = (
        r"component main\s*\{public\s*\[merkleRoot,\s*idx0,\s*idx1,\s*idx2,\s*"
        r"chunkIdx0,\s*chunkIdx1,\s*chunkIdx2\]\}\s*=\s*"
        r"EngramPoSt\(\s*8\s*,\s*3\s*,\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*3\s*\)\s*;"
    )
    replacement = (
        "component main {public [merkleRoot, idx0, idx1, idx2, "
        "chunkIdx0, chunkIdx1, chunkIdx2]} = "
        f"EngramPoSt(8, 3, {shard_bytes_len}, {n_chunks}, {chunk_depth}, 3);"
    )
    generated, count = re.subn(pattern, replacement, source)
    check(count == 1, "Tìm thấy và thay cấu hình component main")
    CIRCUIT_GENERATED.write_text(generated, encoding="utf-8")
    return n_chunks, chunk_depth


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(
            "Usage: PYTHONPATH=src python3 examples/test_real_file_post.py <file_path>"
        )

    file_path = Path(sys.argv[1]).expanduser().resolve()
    if not file_path.is_file():
        raise FileNotFoundError(f"Không tìm thấy file: {file_path}")

    original = file_path.read_bytes()
    if not original:
        raise ValueError("File đầu vào không được rỗng")

    print(f"Input file: {file_path}")
    print(f"Original size: {len(original)} bytes")

    # 1. Encrypt.
    key = generate_key()
    encrypted = encrypt(original, key)
    print(f"Ciphertext size: {len(encrypted.ciphertext)} bytes")

    # 2. RS encode. DATA_SHARDS can be changed to 6 for RS(6, 8).
    encoded = encode(
        encrypted.ciphertext,
        data_shards=DATA_SHARDS,
        total_shards=TOTAL_SHARDS,
    )
    check(encoded.total_shards == TOTAL_SHARDS, "Tạo đủ 8 shard")
    shard_sizes = {len(shard) for shard in encoded.shards}
    check(len(shard_sizes) == 1, "Tất cả shard có cùng độ dài")
    shard_bytes_len = len(encoded.shards[0])
    check(shard_bytes_len > 0, "Shard không rỗng")
    print(f"Shard size: {shard_bytes_len} bytes")

    # 3. Store and read back shards.
    storage_temp = tempfile.TemporaryDirectory(prefix="engram_real_file_post_")
    atexit.register(storage_temp.cleanup)
    storage = StorageManager(storage_temp.name, node_count=TOTAL_SHARDS)
    chunk_id = "real_file_post_test"
    storage.store_chunk(chunk_id, encoded)
    available = storage.load_available_shards(chunk_id)
    check(
        sorted(available) == list(range(TOTAL_SHARDS)),
        "Đọc đủ 8 shard từ StorageManager",
    )
    shards = [available[i] for i in range(TOTAL_SHARDS)]
    check(
        all(len(shard) == shard_bytes_len for shard in shards),
        "Shard đọc từ storage giữ nguyên độ dài",
    )

    # 4. Reconstruct ciphertext and decrypt.
    metadata_storage = storage.get_chunk_metadata(chunk_id)
    recovered_ciphertext = decode(
        available,
        original_size=metadata_storage.original_size,
        data_shards=metadata_storage.data_shards,
        total_shards=metadata_storage.total_shards,
    )
    check(recovered_ciphertext == encrypted.ciphertext, "Khôi phục ciphertext chính xác")
    recovered = decrypt(
        EncryptedPayload(
            ciphertext=recovered_ciphertext,
            nonce=encrypted.nonce,
        ),
        key,
    )
    check(recovered == original, "Giải mã khôi phục đúng file gốc")

    # 5. Challenge selects three shards; each shard has one sampled chunk.
    challenge = random.SystemRandom().sample(range(TOTAL_SHARDS), CHALLENGE_SIZE)
    print(f"Challenge shard indices: {challenge}")

    # 6. Build the nested chunk/shard Merkle commitment and input.
    post_input, tree_metadata = make_post_input(shards, challenge)
    outer_tree = tree_metadata["outer_tree"]
    chunk_indices = tree_metadata["chunk_indices"]
    check(outer_tree.leaf_count == TOTAL_SHARDS, "Outer Merkle tree có 8 shard-root lá")
    print(f"Merkle root: {outer_tree.root}")
    print(f"Challenge chunk indices within selected shards: {chunk_indices}")

    INPUT_JSON.write_text(json.dumps(post_input, indent=2), encoding="utf-8")
    n_chunks, chunk_depth = generate_circuit(shard_bytes_len)

    metadata_output = {
        "source_file": str(file_path),
        "original_size": len(original),
        "ciphertext_size": len(encrypted.ciphertext),
        "data_shards": DATA_SHARDS,
        "total_shards": TOTAL_SHARDS,
        "shard_bytes_len": shard_bytes_len,
        "n_chunks_per_shard": n_chunks,
        "chunk_merkle_depth": chunk_depth,
        "shard_merkle_depth": SHARD_TREE_DEPTH,
        "challenge_shard_indices": challenge,
        "challenge_chunk_indices": chunk_indices,
        "merkle_root": str(outer_tree.root),
        "input_json": str(INPUT_JSON),
        "generated_circuit": str(CIRCUIT_GENERATED),
        "commitment_scheme": "per-shard chunk Merkle trees; outer tree over shard roots",
    }
    METADATA_JSON.write_text(json.dumps(metadata_output, indent=2), encoding="utf-8")

    print(f"\nGenerated input: {INPUT_JSON}")
    print(f"Generated circuit: {CIRCUIT_GENERATED}")
    print(f"Metadata: {METADATA_JSON}")
    print("\n[PASS] Chuẩn bị dữ liệu chunk-level zk-PoSt hoàn tất.")


if __name__ == "__main__":
    main()
