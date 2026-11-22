#!/usr/bin/env bash
set -euo pipefail

# Positive Groth16 cases plus negative witness tests for ENGRAM-SHARD-MERKLE/1.
# Run from any directory. Local development/test setup only; not production ceremony.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

VECTORS="${1:-shard_merkle_vectors.json}"
BUILD="$ROOT/build/shard_merkle_tests"
SHARED="$BUILD/shared"
SNARKJS="$ROOT/node_modules/.bin/snarkjs"
CIRCOM_SOURCE="$ROOT/engram_shard_post.circom"

[[ -f "$VECTORS" ]] || { echo "Missing vectors: $VECTORS" >&2; exit 1; }
[[ -f "$CIRCOM_SOURCE" ]] || { echo "Missing circuit: $CIRCOM_SOURCE" >&2; exit 1; }
[[ -x "$SNARKJS" ]] || { echo "Missing snarkjs. Run npm install." >&2; exit 1; }
command -v circom >/dev/null || { echo "circom is not on PATH" >&2; exit 1; }
command -v openssl >/dev/null || { echo "openssl is required" >&2; exit 1; }

mkdir -p "$BUILD" "$SHARED"

# Reuse the local ptau across test circuits. It is strictly for local tests.
if [[ ! -f "$SHARED/pot14_final.ptau" ]]; then
  "$SNARKJS" powersoftau new bn128 14 "$SHARED/pot14_0000.ptau" -v
  ENTROPY="$(openssl rand -hex 32)"
  "$SNARKJS" powersoftau contribute "$SHARED/pot14_0000.ptau" \
    "$SHARED/pot14_0001.ptau" --name="ENGRAM local test contribution" -e="$ENTROPY" -v
  "$SNARKJS" powersoftau prepare phase2 "$SHARED/pot14_0001.ptau" \
    "$SHARED/pot14_final.ptau" -v
fi

run_positive_case() {
  local case_name="$1" shard_index="$2" chunk_index="$3"
  local case_build="$BUILD/$case_name"
  mkdir -p "$case_build"

  echo
  echo "========== POSITIVE: $case_name (shard=$shard_index, chunk=$chunk_index) =========="
  node shard_merkle_vectors/generate_wrapper.mjs \
    "$VECTORS" "$case_name" "$BUILD/engram_shard_post_main.circom"
  node shard_merkle_vectors/generate_witness_input.mjs \
    "$VECTORS" "$case_name" "$shard_index" "$chunk_index" "$case_build/input.json"

  circom "$BUILD/engram_shard_post_main.circom" \
    --r1cs --wasm --sym -l "$ROOT/node_modules" -o "$case_build"

  local wasm_dir="$case_build/engram_shard_post_main_js"
  node "$wasm_dir/generate_witness.js" \
    "$wasm_dir/engram_shard_post_main.wasm" \
    "$case_build/input.json" "$case_build/witness.wtns"
  "$SNARKJS" wtns check "$case_build/engram_shard_post_main.r1cs" "$case_build/witness.wtns"

  "$SNARKJS" groth16 setup "$case_build/engram_shard_post_main.r1cs" \
    "$SHARED/pot14_final.ptau" "$case_build/circuit_0000.zkey"
  local entropy
  entropy="$(openssl rand -hex 32)"
  "$SNARKJS" zkey contribute "$case_build/circuit_0000.zkey" \
    "$case_build/circuit_final.zkey" --name="ENGRAM local test zkey contribution" -e="$entropy"
  "$SNARKJS" zkey export verificationkey "$case_build/circuit_final.zkey" \
    "$case_build/verification_key.json"
  "$SNARKJS" groth16 prove "$case_build/circuit_final.zkey" \
    "$case_build/witness.wtns" "$case_build/proof.json" "$case_build/public.json"
  "$SNARKJS" groth16 verify "$case_build/verification_key.json" \
    "$case_build/public.json" "$case_build/proof.json"
  echo "PASS: $case_name Groth16 proof verified"
}

# 2 x 32B: two chunks, final chunk is 1 byte; power-of-two outer tree.
run_positive_case "two_shards_32_bytes" 1 1

# 5 x 64B: 3 chunks/shard and 5 leaves in outer tree. Select the final
# shard/chunk to exercise duplicate-last at both inner and outer levels.
run_positive_case "five_shards_64_bytes" 4 2

# Negative tests use the five-shard circuit and a non-duplicate sibling path.
# They must fail during witness generation (constraints unsatisfied).
NEG="$BUILD/negative"
mkdir -p "$NEG"
node shard_merkle_vectors/generate_witness_input.mjs \
  "$VECTORS" "five_shards_64_bytes" 2 1 "$NEG/valid_base.json"
node shard_merkle_vectors/generate_negative_inputs.mjs "$NEG/valid_base.json" "$NEG/inputs"
WASM_DIR="$BUILD/five_shards_64_bytes/engram_shard_post_main_js"
WASM="$WASM_DIR/engram_shard_post_main.wasm"

run_expected_failure() {
  local name="$1"
  local log="$NEG/$name.log"
  echo "[NEGATIVE] $name"
  if node "$WASM_DIR/generate_witness.js" "$WASM" "$NEG/inputs/$name.json" "$NEG/$name.wtns" >"$log" 2>&1; then
    echo "FAIL: invalid input unexpectedly produced a witness: $name" >&2
    cat "$log" >&2
    exit 1
  fi
  echo "PASS: rejected $name"
}

for name in \
  wrong_global_root \
  wrong_chunk_sibling \
  wrong_shard_sibling \
  out_of_range_chunk_index \
  out_of_range_shard_index
do
  run_expected_failure "$name"
done

echo
echo "All ENGRAM Shard Merkle circuit tests passed."
echo "Artifacts: $BUILD"
