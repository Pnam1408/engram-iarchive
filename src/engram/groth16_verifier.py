#engram/groth16_verifier.py
from __future__ import annotations

import json
import subprocess
import re

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from engram.merkle import FIELD_MODULUS
from engram.proof import (
    ChallengeProofResult,
    ShardChallenge,
)

_ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _is_snarkjs_verification_success(
    returncode: int,
    stdout: str,
    stderr: str,
) -> bool:
    if returncode != 0:
        return False

    output = _ANSI_ESCAPE_RE.sub(
        "",
        f"{stdout}\n{stderr}",
    )

    return any(
        line.strip() == "[INFO]  snarkJS: OK!"
        or line.strip().endswith("snarkJS: OK!")
        for line in output.splitlines()
    )


# ---------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------

@dataclass(frozen=True)
class Groth16ProofArtifact:
    """Groth16 proof for exactly one challenged shard chunk."""

    target: ShardChallenge
    p: bytes
    proof_path: Path
    public_path: Path


@dataclass(frozen=True)
class RegisteredGroth16Commitment:
    """Trusted commitment information maintained by Validator."""

    merkle_root: int
    verification_key_path: Path


# ---------------------------------------------------------------------
# Public signal loading
# ---------------------------------------------------------------------

def _load_public_signals(
    path: Path,
) -> tuple[int, int, int]:
    """Load public signals: [merkleRoot, shardIndex, chunkIndex]."""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"Cannot load public signals: {path}"
        ) from exc

    if not isinstance(raw, list) or len(raw) != 3:
        raise ValueError(
            "Expected exactly 3 public signals: "
            "[merkleRoot, shardIndex, chunkIndex]"
        )

    try:
        values = tuple(int(value) for value in raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Public signals must contain integers"
        ) from exc

    if any(value < 0 for value in values):
        raise ValueError("Public signals must be non-negative")

    #All values are int and len == 3, so this is safe to return as tuple[int, int, int]
    return values # pyright: ignore[reportReturnType]


# ---------------------------------------------------------------------
# Single Groth16 proof verification
# ---------------------------------------------------------------------

def verify_groth16_proof(
    artifact: Groth16ProofArtifact,
    commitment: RegisteredGroth16Commitment,
    expected_target: ShardChallenge,
    expected_p: bytes,
    snarkjs_command: tuple[str, ...] = (
        "npx",
        "--no-install",
        "snarkjs",
    ),
    timeout_seconds: float = 120.0,
    cwd=None,
) -> bool:
    """Verify one Groth16 proof against a trusted commitment.

    The circuit public inputs are expected to be:

        [merkleRoot, shardIndex, chunkIndex]

    The challenge p is checked by Validator and is not a circuit
    public input in the current EngramShardPoSt circuit.
    """

    if not isinstance(artifact, Groth16ProofArtifact):
        return False

    if artifact.target != expected_target:
        return False

    if not isinstance(expected_p, bytes) or len(expected_p) != 32:
        return False

    if artifact.p != expected_p:
        return False

    if not isinstance(commitment, RegisteredGroth16Commitment):
        return False

    root = commitment.merkle_root

    if (
        not isinstance(root, int)
        or isinstance(root, bool)
        or not 0 <= root < FIELD_MODULUS
    ):
        return False

    proof_path = Path(artifact.proof_path)
    public_path = Path(artifact.public_path)
    vk_path = Path(commitment.verification_key_path)

    if not proof_path.is_file():
        return False

    if not public_path.is_file():
        return False

    if not vk_path.is_file():
        return False

    # Check that the public signals correspond exactly to the
    # trusted root and the target required by this challenge.
    try:
        public_signals = _load_public_signals(public_path)
    except ValueError:
        return False

    expected_signals = (
        root,
        expected_target.shard_index,
        expected_target.chunk_index,
    )

    if public_signals != expected_signals:
        return False

    # Run actual Groth16 verification.
    command = [
        *snarkjs_command,
        "groth16",
        "verify",
        str(vk_path),
        str(public_path),
        str(proof_path),
    ]

    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )

#        print("\n=== GROTH16 VERIFIER DEBUG ===")
#        print("Command:", command)
#        print("CWD:", cwd)
#        print("Return code:", result.returncode)
#        print("STDOUT:", repr(result.stdout))
#        print("STDERR:", repr(result.stderr))
    except (OSError, subprocess.TimeoutExpired):
        return False

    if result.returncode != 0:
        return False

    return _is_snarkjs_verification_success(
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
    )


# ---------------------------------------------------------------------
# Challenge-level verification
# ---------------------------------------------------------------------

def verify_groth16_challenge(
    challenge_targets: tuple[ShardChallenge, ...],
    proofs: tuple[Groth16ProofArtifact, ...],
    registered_commitments: Mapping[
        str, RegisteredGroth16Commitment
    ],
    expected_p: bytes,
    snarkjs_command: tuple[str, ...] = (
        "npx",
        "--no-install",
        "snarkjs",
    ),
    timeout_seconds: float = 120.0,
    cwd=None,
) -> ChallengeProofResult:
    """Verify all Groth16 proofs required by one challenge.

    The whole challenge is valid only if:
      1. All targets are valid and unique.
      2. Every target has exactly one proof.
      3. No unexpected proof is supplied.
      4. Every commitment is registered.
      5. Every proof uses the expected challenge p.
      6. Public signals match the registered root and target.
      7. Every Groth16 proof passes snarkjs verification.
    """

    if not isinstance(challenge_targets, tuple):
        raise TypeError("challenge_targets must be a tuple")

    if not isinstance(proofs, tuple):
        raise TypeError("proofs must be a tuple")

    if not isinstance(registered_commitments, Mapping):
        raise TypeError("registered_commitments must be a mapping")

    if not isinstance(expected_p, bytes) or len(expected_p) != 32:
        raise ValueError("expected_p must be a 32-byte digest")

    expected_count = len(challenge_targets)

    # Validate challenge targets.
    if any(
        not isinstance(target, ShardChallenge)
        or not target.commitment_id
        or not isinstance(target.shard_index, int)
        or isinstance(target.shard_index, bool)
        or target.shard_index < 0
        or not isinstance(target.chunk_index, int)
        or isinstance(target.chunk_index, bool)
        or target.chunk_index < 0
        for target in challenge_targets
    ):
        raise ValueError("Invalid challenge target")

    if len(set(challenge_targets)) != expected_count:
        raise ValueError("Challenge contains duplicate targets")

    target_set = set(challenge_targets)

    # Index proof artifacts by target.
    proof_map: dict[
        ShardChallenge, Groth16ProofArtifact
    ] = {}

    failed_targets: set[ShardChallenge] = set()

    unexpected_proof_exists = False

    for artifact in proofs:
        if not isinstance(artifact, Groth16ProofArtifact):
            unexpected_proof_exists = True
            continue

        target = artifact.target

        if target not in target_set:
            unexpected_proof_exists = True
            continue

        if target in proof_map:
            failed_targets.add(target)
            continue

        proof_map[target] = artifact

    verified_count = 0

    # Verify every expected target.
    for target in challenge_targets:
        artifact = proof_map.get(target)

        if artifact is None or target in failed_targets:
            failed_targets.add(target)
            continue

        commitment = registered_commitments.get(
            target.commitment_id
        )

        if commitment is None:
            failed_targets.add(target)
            continue

        valid = verify_groth16_proof(
            artifact=artifact,
            commitment=commitment,
            expected_target=target,
            expected_p=expected_p,
            snarkjs_command=snarkjs_command,
            timeout_seconds=timeout_seconds,
            cwd=cwd,
        )

        if valid:
            verified_count += 1
        else:
            failed_targets.add(target)

    valid = (
        verified_count == expected_count
        and not failed_targets
        and not unexpected_proof_exists
        and len(proofs) == expected_count
    )

    return ChallengeProofResult(
        valid=valid,
        verified_count=verified_count,
        expected_count=expected_count,
        failed_targets=tuple(
            target
            for target in challenge_targets
            if target in failed_targets
        ),
    )
