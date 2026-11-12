# engram/proof.py
from dataclasses import dataclass
from typing import Mapping

from engram.merkle import (
    FIELD_MODULUS,
    MerkleProof,
    hash_chunk,
    hash_parent,
    verify_hash_path,
)

# ---------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class ShardChallenge:
    """
    One challenged chunk inside one shard.

    Baseline:
        commitment_id = snapshot/chunk ID

    Incremental:
        commitment_id = generation ID
    """

    commitment_id: str
    shard_index: int
    chunk_index: int


@dataclass(frozen=True)
class ShardWitness:
    """
    Witness required to prove possession of one challenged chunk.

    The Storage Node must retain the complete shard, but only the
    selected chunk and its authentication paths are used here.
    """

    commitment_id: str
    shard_index: int
    chunk_index: int
    chunk_data: bytes
    chunk_merkle_path: MerkleProof
    shard_merkle_path: MerkleProof


@dataclass(frozen=True)
class ShardProof:
    """One proof corresponding to exactly one challenged shard."""

    target: ShardChallenge
    p: bytes
    witness: ShardWitness


@dataclass(frozen=True)
class ChallengeProofResult:
    """Aggregated verification result for an entire challenge."""

    valid: bool
    verified_count: int
    expected_count: int
    failed_targets: tuple[ShardChallenge, ...]


# ---------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------


def _is_valid_field_element(value: object) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value < FIELD_MODULUS
    )


def _validate_target(target: ShardChallenge) -> bool:
    if not isinstance(target, ShardChallenge):
        return False

    return (
        isinstance(target.commitment_id, str)
        and bool(target.commitment_id)
        and isinstance(target.shard_index, int)
        and not isinstance(target.shard_index, bool)
        and target.shard_index >= 0
        and isinstance(target.chunk_index, int)
        and not isinstance(target.chunk_index, bool)
        and target.chunk_index >= 0
    )


def _validate_witness(
    witness: ShardWitness,
    target: ShardChallenge,
) -> bool:
    if not isinstance(witness, ShardWitness):
        return False

    if (
        witness.commitment_id != target.commitment_id
        or witness.shard_index != target.shard_index
        or witness.chunk_index != target.chunk_index
    ):
        return False

    if not isinstance(witness.chunk_data, bytes):
        return False

    if not witness.chunk_data or len(witness.chunk_data) > 31:
        return False

    if not isinstance(witness.chunk_merkle_path, MerkleProof):
        return False

    if not isinstance(witness.shard_merkle_path, MerkleProof):
        return False

    return True


# ---------------------------------------------------------------------
# Proof generation
# ---------------------------------------------------------------------


def generate_shard_proof(
    target: ShardChallenge,
    p: bytes,
    witness: ShardWitness,
) -> ShardProof:
    """
    Generate a proof for one challenged shard.

    This function is storage-independent:
      - Baseline obtains the witness from StorageManager.
      - Incremental obtains the witness from IncrementalStore.

    Current implementation:
        Creates a simulated proof containing the Merkle witness.

    Future implementation:
        The witness will be passed to the Groth16 prover, and the
        returned proof bytes/public inputs will be stored in ShardProof.
    """
    if not _validate_target(target):
        raise ValueError("Invalid challenge target")

    if not isinstance(p, bytes) or len(p) != 32:
        raise ValueError("p must be a 32-byte SHA-256 digest")

    if not _validate_witness(witness, target):
        raise ValueError("Witness does not match the challenge target")

    return ShardProof(
        target=target,
        p=p,
        witness=witness,
    )


# ---------------------------------------------------------------------
# Merkle path verification
# ---------------------------------------------------------------------


def _root_from_path(
    leaf_hash: int,
    proof: MerkleProof,
) -> int | None:
    """
    Reconstruct a Merkle root while validating path structure.

    Assumes the Merkle implementation duplicates the final node
    when a level contains an odd number of nodes.
    """
    if not isinstance(proof, MerkleProof):
        return None

    index = proof.leaf_index
    width = proof.leaf_count

    if (
        not isinstance(index, int)
        or isinstance(index, bool)
        or not isinstance(width, int)
        or isinstance(width, bool)
        or width <= 0
        or not 0 <= index < width
    ):
        return None

    if not _is_valid_field_element(leaf_hash):
        return None

    siblings = proof.siblings

    if not isinstance(siblings, (tuple, list)):
        return None

    # Calculate the expected Merkle path depth.
    expected_depth = 0
    remaining = width

    while remaining > 1:
        expected_depth += 1
        remaining = (remaining + 1) // 2

    if len(siblings) != expected_depth:
        return None

    current = leaf_hash

    for sibling_hash, sibling_is_left in siblings:
        if not _is_valid_field_element(sibling_hash):
            return None

        if not isinstance(sibling_is_left, bool):
            return None

        expected_is_left = index % 2 == 1

        if sibling_is_left != expected_is_left:
            return None

        # Check duplicated final node for an odd-width tree.
        if index % 2 == 0 and index == width - 1 and width % 2 == 1:
            if sibling_hash != current:
                return None

        if sibling_is_left:
            current = hash_parent(sibling_hash, current)
        else:
            current = hash_parent(current, sibling_hash)

        index //= 2
        width = (width + 1) // 2

    return current


# ---------------------------------------------------------------------
# Single-proof verification
# ---------------------------------------------------------------------


def verify_shard_proof(
    proof: ShardProof,
    expected_root: int,
    expected_target: ShardChallenge,
    expected_p: bytes,
    chunks_per_shard: tuple[int, ...],
    total_shards: int,
) -> bool:
    """
    Verify one challenged chunk against a trusted commitment root.

    expected_root must come from the registered commitment,
    not from an untrusted Storage Node.
    """
    if not isinstance(proof, ShardProof):
        return False

    if not _validate_target(expected_target):
        return False

    if not isinstance(expected_p, bytes) or len(expected_p) != 32:
        return False

    if proof.p != expected_p:
        return False

    if proof.target != expected_target:
        return False

    if not _is_valid_field_element(expected_root):
        return False

    if (
        not isinstance(total_shards, int)
        or isinstance(total_shards, bool)
        or total_shards <= 0
    ):
        return False

    if not isinstance(chunks_per_shard, tuple) or len(chunks_per_shard) != total_shards:
        return False

    if any(
        not isinstance(count, int) or isinstance(count, bool) or count <= 0
        for count in chunks_per_shard
    ):
        return False

    target = expected_target

    if target.shard_index >= total_shards:
        return False

    if target.chunk_index >= chunks_per_shard[target.shard_index]:
        return False

    witness = proof.witness

    if not _validate_witness(witness, target):
        return False

    chunk_path = witness.chunk_merkle_path
    shard_path = witness.shard_merkle_path

    # Check chunk Merkle path metadata.
    if chunk_path.leaf_index != target.chunk_index:
        return False

    if chunk_path.leaf_count != chunks_per_shard[target.shard_index]:
        return False

    # Check shard Merkle path metadata.
    if shard_path.leaf_index != target.shard_index:
        return False

    if shard_path.leaf_count != total_shards:
        return False

    try:
        # Step 1: Reconstruct the selected shard's chunk-tree root.
        chunk_root = _root_from_path(
            hash_chunk(witness.chunk_data),
            chunk_path,
        )

        if chunk_root is None:
            return False

        # Step 2: Verify that the shard root belongs to the
        # registered global commitment.
        return verify_hash_path(
            chunk_root,
            shard_path,
            expected_root,
        )

    except (ValueError, TypeError, IndexError, OverflowError):
        return False


# ---------------------------------------------------------------------
# Challenge-level verification
# ---------------------------------------------------------------------


def verify_challenge_proofs(
    challenge_targets: tuple[ShardChallenge, ...],
    proofs: tuple[ShardProof, ...],
    registered_roots: Mapping[str, int],
    expected_p: bytes,
    layouts: Mapping[str, tuple[int, ...]],
) -> ChallengeProofResult:
    """
    Verify and aggregate all proofs required by one challenge.

    Checks:
      1. Challenge targets are valid and unique.
      2. Every target has exactly one proof.
      3. No unexpected or duplicate proof is accepted.
      4. Every commitment exists in the trusted registry.
      5. Every individual proof is valid.

    The whole challenge succeeds only if every required proof succeeds.
    """
    if not isinstance(challenge_targets, tuple):
        raise ValueError("challenge_targets must be a tuple")

    if not isinstance(proofs, tuple):
        raise ValueError("proofs must be a tuple")

    if not isinstance(expected_p, bytes) or len(expected_p) != 32:
        raise ValueError("expected_p must be a 32-byte digest")

    if not isinstance(registered_roots, Mapping):
        raise TypeError("registered_roots must be a mapping")

    if not isinstance(layouts, Mapping):
        raise TypeError("layouts must be a mapping")

    expected_count = len(challenge_targets)

    # Validate challenge targets and detect duplicates.
    target_set = set()

    for target in challenge_targets:
        if not _validate_target(target):
            raise ValueError("Invalid challenge target")

        if target in target_set:
            raise ValueError("Duplicate challenge target")

        target_set.add(target)

    # Index proofs by their target.
    proof_map: dict[ShardChallenge, ShardProof] = {}
    failed_targets = set()

    for proof in proofs:
        if not isinstance(proof, ShardProof):
            # A malformed proof cannot be associated with a target.
            continue

        target = proof.target

        if not _validate_target(target):
            continue

        # Reject proofs that are not required by this challenge.
        if target not in target_set:
            continue

        # Duplicate proof for the same target.
        if target in proof_map:
            failed_targets.add(target)
            continue

        proof_map[target] = proof

    verified_count = 0

    # Verify each required target.
    for target in challenge_targets:
        proof = proof_map.get(target)

        if proof is None or target in failed_targets:
            failed_targets.add(target)
            continue

        commitment_id = target.commitment_id

        # Commitment must have been registered beforehand.
        if commitment_id not in registered_roots:
            failed_targets.add(target)
            continue

        if commitment_id not in layouts:
            failed_targets.add(target)
            continue

        expected_root = registered_roots[commitment_id]
        chunks_per_shard = layouts[commitment_id]

        if not isinstance(chunks_per_shard, tuple):
            failed_targets.add(target)
            continue

        total_shards = len(chunks_per_shard)

        if verify_shard_proof(
            proof=proof,
            expected_root=expected_root,
            expected_target=target,
            expected_p=expected_p,
            chunks_per_shard=chunks_per_shard,
            total_shards=total_shards,
        ):
            verified_count += 1
        else:
            failed_targets.add(target)

    # A challenge is valid only when every required target has
    # exactly one valid proof and there are no unexpected proofs.
    unexpected_proof_exists = any(
        not isinstance(proof, ShardProof)
        or not _validate_target(proof.target)
        or proof.target not in target_set
        for proof in proofs
    )

    valid = (
        verified_count == expected_count
        and not failed_targets
        and len(proofs) == expected_count
        and not unexpected_proof_exists
    )

    return ChallengeProofResult(
        valid=valid,
        verified_count=verified_count,
        expected_count=expected_count,
        failed_targets=tuple(
            target for target in challenge_targets if target in failed_targets
        ),
    )
