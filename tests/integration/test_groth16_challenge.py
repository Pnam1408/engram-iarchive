
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from engram.challenge import create_challenge
from engram.encryption import encrypt, generate_key
from engram.erasure_coding import encode
from engram.proof import ShardChallenge
from engram.storage import StorageManager
from benchmarks.common.workload import deterministic_bytes

from zk.generate_circuit import generate_circuit
from zk.input_builder import build_and_save_single_chunk_input
from engram.groth16_verifier import (
    Groth16ProofArtifact,
    RegisteredGroth16Commitment,
    verify_groth16_challenge,
)


ROOT = Path(__file__).resolve().parents[2]
ZK_DIR = ROOT / "zk"

TEMPLATE = ZK_DIR / "circuits" / "engram_shard_post_template.circom"

DATA_SHARDS = 4
TOTAL_SHARDS = 8
CHALLENGE_SIZE = 3

WORKLOAD_SIZE = 4 * 1024
WORKLOAD_SEED = 20261104

BLOCK_HASH = "integration-test-block"
MINER_ID = "integration-test-miner"
EPOCH_ID = "integration-test-epoch"


def run_command(
    command: list[str],
    cwd: Path,
) -> None:
    """Run an external command and raise on failure."""

    subprocess.run(
        command,
        cwd=cwd,
        check=True,
        text=True,
    )


def require_tool(name: str) -> None:
    if shutil.which(name) is None:
        pytest.skip(f"Required tool not found: {name}")


def test_real_groth16_challenge_verification(
    tmp_path: Path,
):
    """
    Integration test:

        Workload
          -> Encrypt
          -> Reed-Solomon
          -> Storage
          -> Challenge (3 targets)
          -> 3 Circom inputs
          -> 3 Groth16 proofs
          -> Validator verifies all proofs
    """

    # -------------------------------------------------------------
    # 0. Check integration-test prerequisites
    # -------------------------------------------------------------

    if os.getenv("RUN_GROTH16_INTEGRATION") != "1":
        pytest.skip(
            "Set RUN_GROTH16_INTEGRATION=1 to run this test"
        )

    require_tool("circom")
    require_tool("node")
    require_tool("npx")

    ptau_path = Path(
        os.getenv(
            "PTAU_PATH",
            str(ZK_DIR / "build" / "powersOfTau28_hez_final_16.ptau"),
        )
    ).expanduser().resolve()

    if not ptau_path.is_file():
        pytest.skip(f"Powers of Tau file not found: {ptau_path}")

    # -------------------------------------------------------------
    # 1. Generate deterministic workload
    # -------------------------------------------------------------

    original = deterministic_bytes(
        WORKLOAD_SIZE,
        WORKLOAD_SEED,
    )

    assert len(original) == WORKLOAD_SIZE

    # -------------------------------------------------------------
    # 2. Encrypt and encode
    # -------------------------------------------------------------

    key = generate_key()
    encrypted = encrypt(original, key)

    encoded = encode(
        encrypted.ciphertext,
        data_shards=DATA_SHARDS,
        total_shards=TOTAL_SHARDS,
    )

    shards = encoded.shards

    assert len(shards) == TOTAL_SHARDS
    assert len(set(map(len, shards))) == 1
    assert len(shards[0]) > 31

    # -------------------------------------------------------------
    # 3. Store shards and build commitment
    # -------------------------------------------------------------

    storage = StorageManager(
        tmp_path / "storage",
        node_count=TOTAL_SHARDS,
    )

    commitment_id = "integration_generation_0"

    storage.store_chunk(
        commitment_id,
        encoded,
    )

    merkle_root = storage.get_merkle_root(
        commitment_id,
    )

    shard_bytes_len, n_shards = storage.get_shard_layout(
        commitment_id,
    )

    assert n_shards == TOTAL_SHARDS
    assert shard_bytes_len == len(shards[0])

    # -------------------------------------------------------------
    # 4. Create one challenge with three targets
    # -------------------------------------------------------------

    chunks_per_shard = tuple(
        storage.get_chunk_counts(commitment_id)
    )

    challenge = create_challenge(
        chunk_id=commitment_id,
        total_shards=TOTAL_SHARDS,
        chunks_per_shard=chunks_per_shard,
        k=CHALLENGE_SIZE,
        block_hash=BLOCK_HASH,
        miner_id=MINER_ID,
        epoch_id=EPOCH_ID,
    )

    challenge_targets = tuple(
        ShardChallenge(
            commitment_id=commitment_id,
            shard_index=shard_index,
            chunk_index=chunk_index,
        )
        for shard_index, chunk_index in zip(
            challenge.shard_indices,
            challenge.chunk_indices,
        )
    )

    assert len(challenge_targets) == CHALLENGE_SIZE
    assert len(set(challenge_targets)) == CHALLENGE_SIZE

    # -------------------------------------------------------------
    # 5. Generate one circuit for this shard layout
    # -------------------------------------------------------------

    build_dir = tmp_path / "zk" / "build"
    circuit_dir = tmp_path / "zk" / "circuits"

    build_dir.mkdir(parents=True)
    circuit_dir.mkdir(parents=True)

    generated_circuit = (
        circuit_dir / "engram_integration_generated.circom"
    )

    generate_circuit(
        template_path=TEMPLATE,
        output_path=generated_circuit,
        shard_bytes_len=shard_bytes_len,
        n_shards=n_shards,
    )

    # -------------------------------------------------------------
    # 6. Compile Circom circuit
    # -------------------------------------------------------------

    run_command(
        [
            "circom",
            str(generated_circuit),
            "--r1cs",
            "--wasm",
            "--sym",
            "-l",
            str(ZK_DIR / "node_modules"),
            "-o",
            str(build_dir),
        ],
        cwd=ROOT,
    )

    circuit_name = generated_circuit.stem

    r1cs_path = build_dir / f"{circuit_name}.r1cs"
    wasm_path = (
        build_dir
        / f"{circuit_name}_js"
        / f"{circuit_name}.wasm"
    )
    witness_generator = (
        build_dir
        / f"{circuit_name}_js"
        / "generate_witness.js"
    )

    assert r1cs_path.is_file()
    assert wasm_path.is_file()
    assert witness_generator.is_file()

    # -------------------------------------------------------------
    # 7. Groth16 setup (once for the shared circuit)
    # -------------------------------------------------------------

    zkey_path = build_dir / "engram_integration.zkey"
    verification_key_path = (
        build_dir / "engram_integration_verification_key.json"
    )

    run_command(
        [
            "npx",
            "--no-install",
            "snarkjs",
            "groth16",
            "setup",
            str(r1cs_path),
            str(ptau_path),
            str(zkey_path),
        ],
        cwd=ZK_DIR,
    )

    run_command(
        [
            "npx",
            "--no-install",
            "snarkjs",
            "zkey",
            "export",
            "verificationkey",
            str(zkey_path),
            str(verification_key_path),
        ],
        cwd=ZK_DIR,
    )

    assert zkey_path.is_file()
    assert verification_key_path.is_file()

    # -------------------------------------------------------------
    # 8. Generate one proof for each challenged target
    # -------------------------------------------------------------

    artifacts = []

    for proof_number, target in enumerate(
        challenge_targets,
    ):
        proof_dir = build_dir / f"target_{proof_number}"
        proof_dir.mkdir(parents=True)

        input_path = proof_dir / "input.json"

        build_and_save_single_chunk_input(
            storage=storage,
            chunk_id=commitment_id,
            shard_index=target.shard_index,
            chunk_index=target.chunk_index,
            output_path=input_path,
        )

        witness_path = proof_dir / "witness.wtns"
        proof_path = proof_dir / "proof.json"
        public_path = proof_dir / "public.json"

        # Generate witness.
        run_command(
            [
                "node",
                str(witness_generator),
                str(wasm_path),
                str(input_path),
                str(witness_path),
            ],
            cwd=ZK_DIR,
        )

        assert witness_path.is_file()

        # Generate Groth16 proof.
        run_command(
            [
                "npx",
                "--no-install",
                "snarkjs",
                "groth16",
                "prove",
                str(zkey_path),
                str(witness_path),
                str(proof_path),
                str(public_path),
            ],
            cwd=ZK_DIR,
        )

#        import json
#
#        with public_path.open() as f:
#            public_signals = json.load(f)
#
#        print("Expected public signals:")
#        print([
#            str(merkle_root),
#            str(target.shard_index),
#            str(target.chunk_index),
#        ])
#
#        print("Actual public signals:")
#        print(public_signals)

        assert proof_path.is_file()
        assert public_path.is_file()

        artifacts.append(
            Groth16ProofArtifact(
                target=target,
                p=challenge.p,
                proof_path=proof_path,
                public_path=public_path,
            )
        )

    # -------------------------------------------------------------
    # 9. Validator verifies and aggregates all proofs
    # -------------------------------------------------------------

    registered_commitments = {
        commitment_id: RegisteredGroth16Commitment(
            merkle_root=merkle_root,
            verification_key_path=verification_key_path,
        ),
    }

#    print("\n=== DEBUG: direct snarkjs verification ===")
#
#    for artifact in artifacts:
#        print("Target:", artifact.target)
#        print("Proof:", artifact.proof_path)
#        print("Public:", artifact.public_path)
#        print("VK:", verification_key_path)
#
#        subprocess.run(
#            [
#                "npx", "--no-install", "snarkjs",
#                "groth16", "verify",
#                str(verification_key_path),
#                str(artifact.public_path),
#                str(artifact.proof_path),
#            ],
#            cwd=ZK_DIR,
#            check=False,
#            text=True,
#        )

    result = verify_groth16_challenge(
        challenge_targets=challenge_targets,
        proofs=tuple(artifacts),
        registered_commitments=registered_commitments,
        expected_p=challenge.p,
        cwd=ZK_DIR,
    )

    assert result.valid is True
    assert result.verified_count == CHALLENGE_SIZE
    assert result.expected_count == CHALLENGE_SIZE
    assert result.failed_targets == ()

    # -------------------------------------------------------------
    # 10. Tampering tests
    # -------------------------------------------------------------

    def verify_after_tampering():
        return verify_groth16_challenge(
            challenge_targets=challenge_targets,
            proofs=tuple(artifacts),
            registered_commitments=registered_commitments,
            expected_p=challenge.p,
            cwd=ZK_DIR,
        )

    # Chọn proof đầu tiên để thử sửa.
    tampered_artifact = artifacts[0]
    tampered_target = tampered_artifact.target

    # -------------------------------------------------------------
    # 10.1 Tamper public.json
    # -------------------------------------------------------------

    original_public = tampered_artifact.public_path.read_bytes()

    try:
        public_signals = json.loads(original_public)

        # Public signals: [merkleRoot, shardIndex, chunkIndex].
        # Thay đổi shardIndex để không còn khớp với target.
        public_signals[1] = str(int(public_signals[1]) + 1)

        tampered_artifact.public_path.write_text(
            json.dumps(public_signals),
            encoding="utf-8",
        )

        public_result = verify_after_tampering()

        assert public_result.valid is False
        assert tampered_target in public_result.failed_targets
        assert public_result.verified_count == CHALLENGE_SIZE - 1

    finally:
        # Khôi phục file để thử trường hợp tiếp theo.
        tampered_artifact.public_path.write_bytes(original_public)

    # -------------------------------------------------------------
    # 10.2 Tamper proof.json
    # -------------------------------------------------------------

    original_proof = tampered_artifact.proof_path.read_bytes()

    try:
        proof_data = json.loads(original_proof)

        # Thay đổi tọa độ x của điểm pi_a.
        # Public signals không thay đổi, nhưng proof sẽ không còn hợp lệ.
        proof_data["pi_a"][0] = "0"

        tampered_artifact.proof_path.write_text(
            json.dumps(proof_data),
            encoding="utf-8",
        )

        proof_result = verify_after_tampering()

        assert proof_result.valid is False
        assert tampered_target in proof_result.failed_targets
        assert proof_result.verified_count == CHALLENGE_SIZE - 1

    finally:
        tampered_artifact.proof_path.write_bytes(original_proof)
