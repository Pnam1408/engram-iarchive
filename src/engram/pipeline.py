
from engram.encryption import generate_key, encrypt, decrypt, EncryptedPayload
from engram.erasure_coding import encode, decode
from engram.merkle import build_merkle_tree
from engram.storage import StorageManager


def run_demo():
    original = b"Engram-iArchive incremental storage demo"

    # 1. Mã hóa
    key = generate_key()
    encrypted = encrypt(original, key)

    # 2. Phân mảnh
    encoded = encode(
        encrypted.ciphertext,
        data_shards=4,
        total_shards=6,
    )

    # 3. Lưu vào sáu node giả lập
    storage = StorageManager("storage_nodes", node_count=6)
    storage.store_chunk("chunk_0001", encoded)

    print("Stored bytes:", storage.get_stored_bytes())

    # 4. Mô phỏng hai node không khả dụng
    storage.set_node_available(1, False)
    storage.set_node_available(4, False)

    # 5. Đọc các shard còn hợp lệ
    metadata = storage.get_chunk_metadata("chunk_0001")
    available = storage.load_available_shards("chunk_0001")

    # Sắp xếp theo shard index để thứ tự lá luôn nhất quán.
    ordered_indices = sorted(available)
    ordered_shards = [available[i] for i in ordered_indices]
    
    tree = build_merkle_tree(ordered_shards)
    
    print("Merkle root:", tree.root_hex)

    # 6. Khôi phục ciphertext
    recovered_ciphertext = decode(
        available,
        original_size=metadata.original_size,
        data_shards=metadata.data_shards,
        total_shards=metadata.total_shards,
    )

    # 7. Giải mã
    recovered = decrypt(
        EncryptedPayload(
            ciphertext=recovered_ciphertext,
            nonce=encrypted.nonce,
        ),
        key,
    )

    assert recovered == original
    print("Recovery successful:", recovered == original)


if __name__ == "__main__":
    run_demo()
