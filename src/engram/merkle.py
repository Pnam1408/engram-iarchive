
from dataclasses import dataclass

from circomlibpy.poseidon import PoseidonHash


FIELD_MODULUS = (
    21888242871839275222246405745257275088548364400416034343698204186575808495617
)

CHUNK_BYTES = 31
LEAF_DOMAIN = 1
NODE_DOMAIN = 2

Hash = int
_poseidon = PoseidonHash()


def _validate_field_element(value: int, name: str = "value") -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an integer")
    if not 0 <= value < FIELD_MODULUS:
        raise ValueError(f"{name} must be in the BN254 scalar field")


def encode_shard(data: bytes) -> list[int]:
    """Chuyển shard bytes thành các field element, mỗi phần tử tối đa 31 bytes."""
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")

    return [
        int.from_bytes(data[i:i + CHUNK_BYTES], "big")
        for i in range(0, len(data), CHUNK_BYTES)
    ]


def hash_leaf(data: bytes) -> Hash:
    """Hash shard bytes bằng Poseidon-2 theo quy tắc thống nhất với Circom."""
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")

    # Ràng buộc độ dài vào leaf hash để phân biệt các cách chia chunk.
    state = _poseidon.hash(2, [LEAF_DOMAIN, len(data)])

    for chunk in encode_shard(data):
        state = _poseidon.hash(2, [state, chunk])

    return state


def hash_parent(left: Hash, right: Hash) -> Hash:
    """Hash node cha bằng Poseidon-2, có domain separation."""
    _validate_field_element(left, "left")
    _validate_field_element(right, "right")

    state = _poseidon.hash(2, [NODE_DOMAIN, left])
    return _poseidon.hash(2, [state, right])


@dataclass(frozen=True)
class MerkleProof:
    """Bằng chứng Merkle cho một shard."""
    leaf_index: int
    leaf_count: int
    siblings: tuple[tuple[Hash, bool], ...]


@dataclass(frozen=True)
class MerkleTree:
    """Merkle tree lưu các tầng hash từ lá lên root."""
    levels: tuple[tuple[Hash, ...], ...]

    @property
    def root(self) -> Hash:
        return self.levels[-1][0]

    @property
    def root_hex(self) -> str:
        return f"{self.root:064x}"

    @property
    def leaf_count(self) -> int:
        return len(self.levels[0])

    def generate_proof(self, leaf_index: int) -> MerkleProof:
        if not 0 <= leaf_index < self.leaf_count:
            raise IndexError("leaf_index out of range")

        siblings = []
        index = leaf_index

        for level in self.levels[:-1]:
            if index % 2 == 1:
                sibling_index = index - 1
                sibling_is_left = True
            else:
                sibling_index = index + 1

                # Nhân đôi nút cuối nếu tầng có số nút lẻ.
                if sibling_index >= len(level):
                    sibling_index = index

                sibling_is_left = False

            siblings.append((level[sibling_index], sibling_is_left))
            index //= 2

        return MerkleProof(
            leaf_index=leaf_index,
            leaf_count=self.leaf_count,
            siblings=tuple(siblings),
        )


def build_merkle_tree(shards: list[bytes]) -> MerkleTree:
    """Xây Merkle tree từ shard bytes."""
    if not shards:
        raise ValueError("Cannot build a Merkle tree from no shards")

    if any(not isinstance(shard, bytes) for shard in shards):
        raise TypeError("Every shard must be bytes")

    # Circuit hiện tại có một shard length cố định cho mỗi proof.
    if len({len(shard) for shard in shards}) != 1:
        raise ValueError("All shards must have the same byte length")

    leaves = [hash_leaf(shard) for shard in shards]
    levels = [tuple(leaves)]
    current_level = leaves

    while len(current_level) > 1:
        next_level = []

        for i in range(0, len(current_level), 2):
            left = current_level[i]
            right = (
                current_level[i + 1]
                if i + 1 < len(current_level)
                else left
            )
            next_level.append(hash_parent(left, right))

        levels.append(tuple(next_level))
        current_level = next_level

    return MerkleTree(levels=tuple(levels))


def verify_proof(
    shard: bytes,
    proof: MerkleProof,
    expected_root: int,
) -> bool:
    """Xác minh shard bytes với Merkle root Poseidon."""
    if not isinstance(shard, bytes):
        return False
    if not isinstance(proof, MerkleProof):
        return False

    try:
        _validate_field_element(expected_root, "expected_root")
    except (TypeError, ValueError):
        return False

    if proof.leaf_count <= 0:
        return False
    if not 0 <= proof.leaf_index < proof.leaf_count:
        return False

    expected_depth = 0
    width = proof.leaf_count
    while width > 1:
        width = (width + 1) // 2
        expected_depth += 1

    if len(proof.siblings) != expected_depth:
        return False

    current_hash = hash_leaf(shard)
    index = proof.leaf_index
    width = proof.leaf_count

    for sibling_hash, sibling_is_left in proof.siblings:
        try:
            _validate_field_element(sibling_hash, "sibling_hash")
        except (TypeError, ValueError):
            return False

        if not isinstance(sibling_is_left, bool):
            return False

        if sibling_is_left != (index % 2 == 1):
            return False

        if index % 2 == 0 and index + 1 >= width:
            if sibling_hash != current_hash:
                return False

        if sibling_is_left:
            current_hash = hash_parent(sibling_hash, current_hash)
        else:
            current_hash = hash_parent(current_hash, sibling_hash)

        index //= 2
        width = (width + 1) // 2

    return current_hash == expected_root


def hash_chunk(data: bytes) -> Hash:
    """Hash one non-empty chunk (at most 31 bytes), matching the chunk-level Circom leaf."""
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    if not 1 <= len(data) <= CHUNK_BYTES:
        raise ValueError(f"chunk length must be between 1 and {CHUNK_BYTES} bytes")
    state = _poseidon.hash(2, [LEAF_DOMAIN, len(data)])
    return _poseidon.hash(2, [state, int.from_bytes(data, "big")])


def build_hash_tree(leaves: list[Hash]) -> MerkleTree:
    """Build a Merkle tree from already-hashed field-element leaves."""
    if not leaves:
        raise ValueError("Cannot build a Merkle tree from no leaves")
    for i, leaf in enumerate(leaves):
        _validate_field_element(leaf, f"leaves[{i}]")

    levels = [tuple(leaves)]
    current_level = list(leaves)
    while len(current_level) > 1:
        next_level = []
        for i in range(0, len(current_level), 2):
            left = current_level[i]
            right = current_level[i + 1] if i + 1 < len(current_level) else left
            next_level.append(hash_parent(left, right))
        levels.append(tuple(next_level))
        current_level = next_level
    return MerkleTree(levels=tuple(levels))


def build_chunk_merkle_tree(shard: bytes) -> MerkleTree:
    """Build a per-shard Merkle tree whose leaves commit to individual 31-byte chunks."""
    if not isinstance(shard, bytes):
        raise TypeError("shard must be bytes")
    if not shard:
        raise ValueError("shard must not be empty")
    chunks = [
        shard[i:i + CHUNK_BYTES]
        for i in range(0, len(shard), CHUNK_BYTES)
    ]
    return build_hash_tree([hash_chunk(chunk) for chunk in chunks])


def verify_hash_path(
    leaf_hash: Hash,
    proof: MerkleProof,
    expected_root: Hash,
) -> bool:
    """Verify a Merkle path when the leaf is already represented by its hash."""
    if not isinstance(proof, MerkleProof):
        return False
    try:
        _validate_field_element(leaf_hash, "leaf_hash")
        _validate_field_element(expected_root, "expected_root")
    except (TypeError, ValueError):
        return False
    if not 0 <= proof.leaf_index < proof.leaf_count:
        return False

    current_hash = leaf_hash
    index = proof.leaf_index
    width = proof.leaf_count
    for sibling_hash, sibling_is_left in proof.siblings:
        try:
            _validate_field_element(sibling_hash, "sibling_hash")
        except (TypeError, ValueError):
            return False
        if not isinstance(sibling_is_left, bool):
            return False
        if sibling_is_left != (index % 2 == 1):
            return False
        if index % 2 == 0 and index + 1 >= width and sibling_hash != current_hash:
            return False
        current_hash = (
            hash_parent(sibling_hash, current_hash)
            if sibling_is_left
            else hash_parent(current_hash, sibling_hash)
        )
        index //= 2
        width = (width + 1) // 2
    return current_hash == expected_root
