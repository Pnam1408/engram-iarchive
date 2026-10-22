
import json
from pathlib import Path

from engram.merkle import build_merkle_tree, encode_shard, verify_proof


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

def main():
    # Phải khớp với dữ liệu giả lập trong generate_post_input.py
    shards = [bytes([i]) * 31 for i in range(1, 9)]
    indices = [1, 4, 6]

    input_path = Path("zk/input.json")
    with input_path.open(encoding="utf-8") as f:
        actual_input = json.load(f)

    # 1. Tái tạo input và so sánh toàn bộ dữ liệu
    expected_input = make_post_input(shards, indices)

    if actual_input != expected_input:
        print("[FAIL] input.json không khớp dữ liệu Python đã tạo")
        return

    print("[PASS] input.json khớp dữ liệu shard, root và Merkle paths")

    # 2. Kiểm tra root và từng Merkle proof độc lập
    tree = build_merkle_tree(shards)

    print("Merkle root:", tree.root)
    for index in indices:
        proof = tree.generate_proof(index)
        ok = verify_proof(shards[index], proof, tree.root)
        print(f"Shard {index}: {'PASS' if ok else 'FAIL'}")

        if not ok:
            return

    # 3. Thử dùng dữ liệu shard sai
    tampered_shard = bytearray(shards[indices[0]])
    tampered_shard[0] ^= 1

    proof = tree.generate_proof(indices[0])
    tampered_ok = verify_proof(
        bytes(tampered_shard), proof, tree.root
    )

    print(
        "Shard bị sửa:",
        "FAIL (đúng như mong đợi)" if not tampered_ok
        else "ERROR: dữ liệu sai vẫn được chấp nhận"
    )

    if tampered_ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
