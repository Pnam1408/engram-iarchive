
import atexit
import json
import random
import re
import sys
import tempfile
from pathlib import Path

from engram.encryption import generate_key, encrypt, decrypt, EncryptedPayload
from engram.erasure_coding import encode, decode
from engram.merkle import build_merkle_tree, encode_shard, verify_proof
from engram.storage import StorageManager

def make_post_input(shards: list[bytes], indices: list[int]) -> dict:
    """Tạo witness input cho circuit EngramPoSt(8, 3, L, N)."""
    if len(shards) != 8:
        raise ValueError("This circuit expects exactly 8 shards")

    if len(indices) != 3 or len(set(indices)) != 3:
        raise ValueError("Provide 3 distinct shard indices")

    if any(not 0 <= i < len(shards) for i in indices):
        raise ValueError("Shard index out of range")

    if len({len(shard) for shard in shards}) != 1:
        raise ValueError("All shards must have the same length")

    tree = build_merkle_tree(shards)

    selected_chunks = []
    selected_paths = []

    for index in indices:
        selected_chunks.append(
            [str(value) for value in encode_shard(shards[index])]
        )

        proof = tree.generate_proof(index)
        selected_paths.append(
            [str(sibling_hash) for sibling_hash, _ in proof.siblings]
        )

    return {
        "merkleRoot": str(tree.root),
        "idx0": str(indices[0]),
        "idx1": str(indices[1]),
        "idx2": str(indices[2]),
        "shardChunks": selected_chunks,
        "merklePaths": selected_paths,
    }


ROOT = Path(__file__).resolve().parents[1]
ZK_DIR = ROOT / "zk"
CIRCUIT_TEMPLATE = ZK_DIR / "circuits" / "engram_post.circom"
CIRCUIT_GENERATED = ZK_DIR / "circuits" / "engram_post_real_file.circom"
INPUT_JSON = ZK_DIR / "input_real_file.json"
METADATA_JSON = ZK_DIR / "test_real_file_metadata.json"

DATA_SHARDS = 4
TOTAL_SHARDS = 8
CHALLENGE_SIZE = 3


def check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(f"[FAIL] {message}")
    print(f"[PASS] {message}")


def generate_circuit(shard_bytes_len: int) -> int:
    """Tạo circuit với độ dài shard đúng bằng dữ liệu thực tế."""
    n_chunks = (shard_bytes_len + 30) // 31

    source = CIRCUIT_TEMPLATE.read_text(encoding="utf-8")

    pattern = (
        r"component main\s*"
        r"\{public\s*\[merkleRoot,\s*idx0,\s*idx1,\s*idx2\]\}\s*"
        r"=\s*EngramPoSt\(\s*8\s*,\s*3\s*,\s*\d+\s*,\s*\d+\s*\)\s*;"
    )

    replacement = (
        "component main {public [merkleRoot, idx0, idx1, idx2]} = "
        f"EngramPoSt(8, 3, {shard_bytes_len}, {n_chunks});"
    )

    generated, count = re.subn(pattern, replacement, source)

    check(count == 1, "Tìm thấy và thay cấu hình component main")
    CIRCUIT_GENERATED.write_text(generated, encoding="utf-8")

    return n_chunks


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(
            "Cách dùng: PYTHONPATH=src python3 "
            "examples/test_real_file_post.py <đường_dẫn_file>"
        )

    file_path = Path(sys.argv[1]).expanduser().resolve()

    if not file_path.is_file():
        raise FileNotFoundError(f"Không tìm thấy file: {file_path}")

    original = file_path.read_bytes()

    if not original:
        raise ValueError("File đầu vào không được rỗng")

    print(f"Input file: {file_path}")
    print(f"Original size: {len(original)} bytes")

    # 1. Mã hóa file thật.
    key = generate_key()
    encrypted = encrypt(original, key)

    print(f"Ciphertext size: {len(encrypted.ciphertext)} bytes")

    # 2. Phân mảnh thành 8 shard.
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

    # 3. Lưu shard vào StorageManager.
    storage_temp = tempfile.TemporaryDirectory(prefix="engram_real_file_post_")
    atexit.register(storage_temp.cleanup)
    storage = StorageManager(
        storage_temp.name,
        node_count=TOTAL_SHARDS,
    )

    chunk_id = "real_file_post_test"
    storage.store_chunk(chunk_id, encoded)

    available = storage.load_available_shards(chunk_id)
    indices = sorted(available)

    check(
        indices == list(range(TOTAL_SHARDS)),
        "Đọc đủ 8 shard từ StorageManager",
    )

    shards = [available[i] for i in indices]

    check(
        all(len(shard) == shard_bytes_len for shard in shards),
        "Shard đọc từ storage giữ nguyên độ dài",
    )

    # 4. Kiểm tra khả năng khôi phục ciphertext và file gốc.
    metadata = storage.get_chunk_metadata(chunk_id)

    recovered_ciphertext = decode(
        available,
        original_size=metadata.original_size,
        data_shards=metadata.data_shards,
        total_shards=metadata.total_shards,
    )

    check(
        recovered_ciphertext == encrypted.ciphertext,
        "Khôi phục ciphertext chính xác",
    )

    recovered = decrypt(
        EncryptedPayload(
            ciphertext=recovered_ciphertext,
            nonce=encrypted.nonce,
        ),
        key,
    )

    check(recovered == original, "Giải mã khôi phục đúng file gốc")

    # 5. Dựng Merkle tree từ 8 shard thực tế.
    tree = build_merkle_tree(shards)

    check(tree.leaf_count == TOTAL_SHARDS, "Merkle tree có 8 lá")

    print(f"Merkle root: {tree.root}")

    # 6. Sinh challenge gồm 3 chỉ số khác nhau.
    challenge = random.SystemRandom().sample(
        range(TOTAL_SHARDS),
        CHALLENGE_SIZE,
    )

    print(f"Challenge indices: {challenge}")

    # 7. Kiểm tra Merkle proof độc lập bằng Python.
    for index in challenge:
        proof = tree.generate_proof(index)

        check(
            verify_proof(shards[index], proof, tree.root),
            f"Python xác minh Merkle proof cho shard {index}",
        )

    # 8. Sinh input cho Circom từ chính các shard đã lưu.
    post_input = make_post_input(shards, challenge)

    check(
        int(post_input["merkleRoot"]) == tree.root,
        "Merkle root trong input khớp với cây Merkle",
    )

    INPUT_JSON.write_text(
        json.dumps(post_input, indent=2),
        encoding="utf-8",
    )

    n_chunks = generate_circuit(shard_bytes_len)

    metadata_output = {
        "source_file": str(file_path),
        "original_size": len(original),
        "ciphertext_size": len(encrypted.ciphertext),
        "data_shards": DATA_SHARDS,
        "total_shards": TOTAL_SHARDS,
        "shard_bytes_len": shard_bytes_len,
        "n_chunks": n_chunks,
        "challenge_indices": challenge,
        "merkle_root": str(tree.root),
        "input_json": str(INPUT_JSON),
        "generated_circuit": str(CIRCUIT_GENERATED),
    }

    METADATA_JSON.write_text(
        json.dumps(metadata_output, indent=2),
        encoding="utf-8",
    )

    print(f"\nGenerated input: {INPUT_JSON}")
    print(f"Generated circuit: {CIRCUIT_GENERATED}")
    print(f"Metadata: {METADATA_JSON}")
    print("\n[PASS] Chuẩn bị dữ liệu thực cho zk-PoSt hoàn tất.")


if __name__ == "__main__":
    main()
