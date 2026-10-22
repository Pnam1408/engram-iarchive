from engram.challenge import create_challenge

chunks_per_shard = (17, 17, 17, 17, 17, 17, 17, 17)

challenge = create_challenge(
    chunk_id="multi_chunk",
    total_shards=8,
    chunks_per_shard=chunks_per_shard,
    k=3,
)

print("p:", challenge.p.hex())
print("Shard indices:", challenge.shard_indices)
print("Chunk indices:", challenge.chunk_indices)

assert len(challenge.shard_indices) == 3
assert len(set(challenge.shard_indices)) == 3
assert len(challenge.chunk_indices) == 3

for shard_index, chunk_index in zip(
    challenge.shard_indices,
    challenge.chunk_indices,
):
    assert 0 <= shard_index < len(chunks_per_shard)
    assert 0 <= chunk_index < chunks_per_shard[shard_index]

# Cùng đầu vào phải cho kết quả giống nhau.
challenge2 = create_challenge(
    chunk_id="multi_chunk",
    total_shards=8,
    chunks_per_shard=chunks_per_shard,
    k=3,
)

assert challenge == challenge2

print("Challenge test passed.")