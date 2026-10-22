
from engram.challenge import create_challenge
from engram.erasure_coding import encode
from engram.storage import StorageManager
from engram.proof import generate_proof, verify_storage_proof


def main():
    chunk_id = "chunk_demo"
    total_shards = 6
    k = 3

    # 1. Prepare sample data and store its shards.
    storage = StorageManager("demo_storage", node_count=total_shards)

    encoded = encode(
        b"ENGRAM incremental storage simulation",
        data_shards=4,
        total_shards=total_shards,
    )
    storage.store_chunk(chunk_id, encoded)

    # 2. Derive p and select k shard indices.
    challenge = create_challenge(
        chunk_id=chunk_id,
        total_shards=total_shards,
        k=k,
    )

    # 3. Generate the simulated proof.
    proof = generate_proof(
        storage=storage,
        chunk_id=chunk_id,
        p=challenge.p,
        total_shards=total_shards,
        k=k,
    )

    # 4. Verify against a trusted Merkle root and p.
    trusted_root = storage.get_merkle_root(chunk_id)

    valid = verify_storage_proof(
        proof=proof,
        expected_root=trusted_root,
        expected_p=challenge.p,
        total_shards=total_shards,
        k=k,
    )

    print("p:", challenge.p.hex())
    print("Selected shard indices:", challenge.shard_indices)
    print("Number of witnesses:", len(proof.witnesses))
    print("Proof valid:", valid)


if __name__ == "__main__":
    main()
