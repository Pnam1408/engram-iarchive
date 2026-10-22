from circomlibpy.poseidon import PoseidonHash

def calculate_hash():

    poseidon = PoseidonHash()

    # Hash một shard
    leaf = poseidon.hash(1, [11])
    print("Leaf:", leaf)

    # Hash hai giá trị
    parent = poseidon.hash(2, [12345, 67890])
    print("Parent:", parent)

if __name__ == "__main__":
    calculate_hash()
