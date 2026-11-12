# tests/test_groth16_verifier.py

import json
from pathlib import Path
from subprocess import TimeoutExpired
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from engram.proof import (
    ChallengeProofResult,
    ShardChallenge,
)
from engram.groth16_verifier import (
    Groth16ProofArtifact,
    RegisteredGroth16Commitment,
    verify_groth16_proof,
    verify_groth16_challenge,
    _is_snarkjs_verification_success,
)

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

TEST_P = bytes.fromhex("ab" * 32)
TEST_ROOT = 123456789

SNARKJS_COMMAND = ("snarkjs",)


def make_target(
    commitment_id: str = "generation_0",
    shard_index: int = 0,
    chunk_index: int = 0,
) -> ShardChallenge:
    return ShardChallenge(
        commitment_id=commitment_id,
        shard_index=shard_index,
        chunk_index=chunk_index,
    )


def make_artifact(
    tmp_path: Path,
    target: ShardChallenge,
    p: bytes = TEST_P,
    root: int = TEST_ROOT,
    public_signals: list | None = None,
    name: str = "proof",
) -> Groth16ProofArtifact:
    """Create dummy proof/public/VK files for unit testing."""

    proof_path = tmp_path / f"{name}_proof.json"
    public_path = tmp_path / f"{name}_public.json"

    proof_path.write_text(
        json.dumps({"pi_a": ["1", "2"]}),
        encoding="utf-8",
    )

    if public_signals is None:
        public_signals = [
            str(root),
            str(target.shard_index),
            str(target.chunk_index),
        ]

    public_path.write_text(
        json.dumps(public_signals),
        encoding="utf-8",
    )

    return Groth16ProofArtifact(
        target=target,
        p=p,
        proof_path=proof_path,
        public_path=public_path,
    )


def make_commitment(
    tmp_path: Path,
    root: int = TEST_ROOT,
    name: str = "vk",
) -> RegisteredGroth16Commitment:
    vk_path = tmp_path / f"{name}.json"

    vk_path.write_text(
        json.dumps({"protocol": "groth16"}),
        encoding="utf-8",
    )

    return RegisteredGroth16Commitment(
        merkle_root=root,
        verification_key_path=vk_path,
    )


def snarkjs_success():
    return SimpleNamespace(
        returncode=0,
        stdout="[INFO]  snarkJS: OK!\n",
        stderr="",
    )


def snarkjs_failure():
    return SimpleNamespace(
        returncode=1,
        stdout="",
        stderr="Invalid proof",
    )


def make_three_targets():
    return (
        make_target(shard_index=0, chunk_index=2),
        make_target(shard_index=2, chunk_index=4),
        make_target(shard_index=5, chunk_index=1),
    )


def make_three_artifacts(
    tmp_path: Path,
    targets: tuple[ShardChallenge, ...],
):
    return tuple(
        make_artifact(
            tmp_path=tmp_path,
            target=target,
            name=f"proof_{index}",
        )
        for index, target in enumerate(targets)
    )


# ---------------------------------------------------------------------
# Single-proof verification
# ---------------------------------------------------------------------


class TestVerifyGroth16Proof:

    def test_valid_proof(self, tmp_path):
        target = make_target(
            shard_index=2,
            chunk_index=4,
        )

        artifact = make_artifact(
            tmp_path,
            target,
        )

        commitment = make_commitment(tmp_path)

        with patch(
            "engram.groth16_verifier.subprocess.run",
            return_value=snarkjs_success(),
        ) as mock_run:

            result = verify_groth16_proof(
                artifact=artifact,
                commitment=commitment,
                expected_target=target,
                expected_p=TEST_P,
                snarkjs_command=SNARKJS_COMMAND,
            )

        assert result is True
        mock_run.assert_called_once()

    def test_wrong_target(self, tmp_path):
        target = make_target(
            shard_index=2,
            chunk_index=4,
        )

        wrong_target = make_target(
            shard_index=3,
            chunk_index=4,
        )

        artifact = make_artifact(
            tmp_path,
            target,
        )

        commitment = make_commitment(tmp_path)

        with patch(
            "engram.groth16_verifier.subprocess.run",
        ) as mock_run:

            result = verify_groth16_proof(
                artifact=artifact,
                commitment=commitment,
                expected_target=wrong_target,
                expected_p=TEST_P,
            )

        assert result is False
        mock_run.assert_not_called()

    def test_wrong_challenge_p(self, tmp_path):
        target = make_target()

        artifact = make_artifact(
            tmp_path,
            target,
        )

        commitment = make_commitment(tmp_path)

        wrong_p = bytes.fromhex("cd" * 32)

        with patch(
            "engram.groth16_verifier.subprocess.run",
        ) as mock_run:

            result = verify_groth16_proof(
                artifact=artifact,
                commitment=commitment,
                expected_target=target,
                expected_p=wrong_p,
            )

        assert result is False
        mock_run.assert_not_called()

    def test_wrong_public_merkle_root(self, tmp_path):
        target = make_target()

        artifact = make_artifact(
            tmp_path,
            target,
            public_signals=[
                "999999",
                str(target.shard_index),
                str(target.chunk_index),
            ],
        )

        commitment = make_commitment(
            tmp_path,
            root=TEST_ROOT,
        )

        with patch(
            "engram.groth16_verifier.subprocess.run",
        ) as mock_run:

            result = verify_groth16_proof(
                artifact=artifact,
                commitment=commitment,
                expected_target=target,
                expected_p=TEST_P,
            )

        assert result is False
        mock_run.assert_not_called()

    @pytest.mark.parametrize(
        "wrong_signals",
        [
            ["123456789", "3", "0"],
            ["123456789", "0", "5"],
            ["123456789", "0"],
            ["123456789", "0", "0", "1"],
        ],
    )
    def test_wrong_public_signals(self, tmp_path, wrong_signals):
        target = make_target()

        artifact = make_artifact(
            tmp_path,
            target,
            public_signals=wrong_signals,
        )

        commitment = make_commitment(tmp_path)

        with patch(
            "engram.groth16_verifier.subprocess.run",
        ) as mock_run:

            result = verify_groth16_proof(
                artifact=artifact,
                commitment=commitment,
                expected_target=target,
                expected_p=TEST_P,
            )

        assert result is False
        mock_run.assert_not_called()

    def test_snarkjs_invalid_proof(self, tmp_path):
        target = make_target()

        artifact = make_artifact(
            tmp_path,
            target,
        )

        commitment = make_commitment(tmp_path)

        with patch(
            "engram.groth16_verifier.subprocess.run",
            return_value=snarkjs_failure(),
        ):

            result = verify_groth16_proof(
                artifact=artifact,
                commitment=commitment,
                expected_target=target,
                expected_p=TEST_P,
                snarkjs_command=SNARKJS_COMMAND,
            )

        assert result is False

    def test_snarkjs_timeout(self, tmp_path):
        target = make_target()

        artifact = make_artifact(
            tmp_path,
            target,
        )

        commitment = make_commitment(tmp_path)

        with patch(
            "engram.groth16_verifier.subprocess.run",
            side_effect=TimeoutExpired(
                cmd="snarkjs",
                timeout=1,
            ),
        ):

            result = verify_groth16_proof(
                artifact=artifact,
                commitment=commitment,
                expected_target=target,
                expected_p=TEST_P,
                timeout_seconds=1,
            )

        assert result is False

    def test_missing_proof_file(self, tmp_path):
        target = make_target()

        artifact = make_artifact(
            tmp_path,
            target,
        )

        artifact.proof_path.unlink()

        commitment = make_commitment(tmp_path)

        with patch(
            "engram.groth16_verifier.subprocess.run",
        ) as mock_run:

            result = verify_groth16_proof(
                artifact=artifact,
                commitment=commitment,
                expected_target=target,
                expected_p=TEST_P,
            )

        assert result is False
        mock_run.assert_not_called()


# ---------------------------------------------------------------------
# Challenge-level verification
# ---------------------------------------------------------------------


class TestVerifyGroth16Challenge:

    def test_all_proofs_valid(self, tmp_path):
        targets = make_three_targets()
        artifacts = make_three_artifacts(
            tmp_path,
            targets,
        )

        commitments = {
            "generation_0": make_commitment(tmp_path),
        }

        with patch(
            "engram.groth16_verifier.subprocess.run",
            return_value=snarkjs_success(),
        ) as mock_run:

            result = verify_groth16_challenge(
                challenge_targets=targets,
                proofs=artifacts,
                registered_commitments=commitments,
                expected_p=TEST_P,
                snarkjs_command=SNARKJS_COMMAND,
            )

        assert isinstance(result, ChallengeProofResult)
        assert result.valid is True
        assert result.verified_count == 3
        assert result.expected_count == 3
        assert result.failed_targets == ()

        assert mock_run.call_count == 3

    def test_one_invalid_proof(self, tmp_path):
        targets = make_three_targets()
        artifacts = make_three_artifacts(
            tmp_path,
            targets,
        )

        commitments = {
            "generation_0": make_commitment(tmp_path),
        }

        def mock_verify(*args, **kwargs):
            # Fail the second verification.
            if mock_verify.call_count == 1:
                mock_verify.call_count += 1
                return snarkjs_failure()

            mock_verify.call_count += 1
            return snarkjs_success()

        mock_verify.call_count = 0

        with patch(
            "engram.groth16_verifier.subprocess.run",
            side_effect=mock_verify,
        ):

            result = verify_groth16_challenge(
                challenge_targets=targets,
                proofs=artifacts,
                registered_commitments=commitments,
                expected_p=TEST_P,
                snarkjs_command=SNARKJS_COMMAND,
            )

        assert result.valid is False
        assert result.verified_count == 2
        assert result.expected_count == 3
        assert targets[1] in result.failed_targets

    def test_missing_proof(self, tmp_path):
        targets = make_three_targets()
        artifacts = make_three_artifacts(
            tmp_path,
            targets,
        )

        commitments = {
            "generation_0": make_commitment(tmp_path),
        }

        with patch(
            "engram.groth16_verifier.subprocess.run",
            return_value=snarkjs_success(),
        ):

            result = verify_groth16_challenge(
                challenge_targets=targets,
                proofs=artifacts[:2],
                registered_commitments=commitments,
                expected_p=TEST_P,
                snarkjs_command=SNARKJS_COMMAND,
            )

        assert result.valid is False
        assert result.verified_count == 2
        assert result.expected_count == 3
        assert targets[2] in result.failed_targets

    def test_duplicate_proof_target(self, tmp_path):
        targets = make_three_targets()
        artifacts = make_three_artifacts(
            tmp_path,
            targets,
        )

        proofs = (
            artifacts[0],
            artifacts[0],
            artifacts[1],
        )

        commitments = {
            "generation_0": make_commitment(tmp_path),
        }

        with patch(
            "engram.groth16_verifier.subprocess.run",
            return_value=snarkjs_success(),
        ):

            result = verify_groth16_challenge(
                challenge_targets=targets,
                proofs=proofs,
                registered_commitments=commitments,
                expected_p=TEST_P,
                snarkjs_command=SNARKJS_COMMAND,
            )

        assert result.valid is False
        assert targets[0] in result.failed_targets
        assert targets[2] in result.failed_targets

    def test_unexpected_proof(self, tmp_path):
        targets = make_three_targets()
        artifacts = make_three_artifacts(
            tmp_path,
            targets,
        )

        unexpected_target = make_target(
            commitment_id="generation_1",
            shard_index=7,
            chunk_index=3,
        )

        unexpected_artifact = make_artifact(
            tmp_path,
            unexpected_target,
            name="unexpected",
        )

        proofs = (
            artifacts[0],
            artifacts[1],
            unexpected_artifact,
        )

        commitments = {
            "generation_0": make_commitment(tmp_path),
            "generation_1": make_commitment(
                tmp_path,
                name="vk_1",
            ),
        }

        with patch(
            "engram.groth16_verifier.subprocess.run",
            return_value=snarkjs_success(),
        ):

            result = verify_groth16_challenge(
                challenge_targets=targets,
                proofs=proofs,
                registered_commitments=commitments,
                expected_p=TEST_P,
                snarkjs_command=SNARKJS_COMMAND,
            )

        assert result.valid is False
        assert targets[2] in result.failed_targets

    def test_unknown_commitment(self, tmp_path):
        targets = make_three_targets()
        artifacts = make_three_artifacts(
            tmp_path,
            targets,
        )

        commitments = {
            "generation_0": make_commitment(tmp_path),
        }

        # The target references a commitment that is not registered.
        unknown_target = ShardChallenge(
            commitment_id="unknown_generation",
            shard_index=1,
            chunk_index=2,
        )

        targets = (
            targets[0],
            targets[1],
            unknown_target,
        )

        artifacts = (
            artifacts[0],
            artifacts[1],
            make_artifact(
                tmp_path,
                unknown_target,
                name="unknown",
            ),
        )

        with patch(
            "engram.groth16_verifier.subprocess.run",
            return_value=snarkjs_success(),
        ) as mock_run:

            result = verify_groth16_challenge(
                challenge_targets=targets,
                proofs=artifacts,
                registered_commitments=commitments,
                expected_p=TEST_P,
                snarkjs_command=SNARKJS_COMMAND,
            )

        assert result.valid is False
        assert unknown_target in result.failed_targets

        # Only the two registered commitments are verified.
        assert mock_run.call_count == 2

    def test_wrong_p_in_one_artifact(self, tmp_path):
        targets = make_three_targets()
        artifacts = list(make_three_artifacts(tmp_path, targets))

        artifacts[1] = Groth16ProofArtifact(
            target=artifacts[1].target,
            p=bytes.fromhex("cd" * 32),
            proof_path=artifacts[1].proof_path,
            public_path=artifacts[1].public_path,
        )

        commitments = {
            "generation_0": make_commitment(tmp_path),
        }

        with patch(
            "engram.groth16_verifier.subprocess.run",
            return_value=snarkjs_success(),
        ):

            result = verify_groth16_challenge(
                challenge_targets=targets,
                proofs=tuple(artifacts),
                registered_commitments=commitments,
                expected_p=TEST_P,
                snarkjs_command=SNARKJS_COMMAND,
            )

        assert result.valid is False
        assert result.verified_count == 2
        assert targets[1] in result.failed_targets

    def test_duplicate_challenge_targets_rejected(self, tmp_path):
        target = make_target()

        with pytest.raises(
            ValueError,
            match="duplicate",
        ):
            verify_groth16_challenge(
                challenge_targets=(target, target),
                proofs=(),
                registered_commitments={},
                expected_p=TEST_P,
            )

    def test_invalid_expected_p_rejected(self):
        with pytest.raises(
            ValueError,
            match="32-byte",
        ):
            verify_groth16_challenge(
                challenge_targets=(),
                proofs=(),
                registered_commitments={},
                expected_p=b"invalid",
            )

    def test_snarkjs_success_plain_output(self):
        assert _is_snarkjs_verification_success(
            0,
            "[INFO]  snarkJS: OK!\n",
            "",
        )

    def test_snarkjs_success_ansi_output(self):
        assert _is_snarkjs_verification_success(
            0,
            "\x1b[32;22m[INFO]  \x1b[39;1msnarkJS\x1b[0m: OK!\n",
            "",
        )

    def test_snarkjs_failure_nonzero_returncode(self):
        assert not _is_snarkjs_verification_success(
            1,
            "[INFO]  snarkJS: OK!\n",
            "",
        )

    def test_snarkjs_failure_invalid_proof(self):
        assert not _is_snarkjs_verification_success(
            0,
            "[ERROR]  snarkJS: Invalid proof\n",
            "",
        )
