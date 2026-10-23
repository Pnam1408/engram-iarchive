#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: bash run_real_file_post.sh <path-to-input-file>"
  exit 1
fi

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
PTAU_PATH="${PTAU_PATH:-$ROOT/zk/build/powersOfTau28_hez_final_16.ptau}"

if [[ ! -f "$PTAU_PATH" ]]; then
  echo "Missing Powers of Tau file: $PTAU_PATH" >&2
  echo "Set PTAU_PATH=/absolute/path/to/powersOfTau28_hez_final_16.ptau and retry." >&2
  exit 2
fi

mkdir -p zk/build

echo "=== 1. Prepare real-file pipeline and circuit input ==="
PYTHONPATH=src python3 examples/test_real_file_post.py "$1"

echo "=== 2. Compile generated Circom circuit ==="
cd "$ROOT/zk"
circom circuits/engram_post_generated.circom --r1cs --wasm --sym -l node_modules -o build

echo "=== 3. Groth16 setup (one-time; excluded from benchmark timings) ==="
npx --no-install snarkjs groth16 setup \
  build/engram_post_generated.r1cs \
  "$PTAU_PATH" \
  build/engram_post_generated.zkey
npx --no-install snarkjs zkey export verificationkey \
  build/engram_post_generated.zkey \
  build/engram_post_generated_verification_key.json

echo "=== 4. Generate witness ==="
node build/engram_post_generated_js/generate_witness.js \
  build/engram_post_generated_js/engram_post_generated.wasm \
  input_real_file.json \
  build/witness_real_file.wtns

echo "=== 5. Generate Groth16 proof ==="
npx --no-install snarkjs groth16 prove \
  build/engram_post_generated.zkey \
  build/witness_real_file.wtns \
  build/proof_real_file.json \
  build/public_real_file.json

echo "=== 6. Verify proof ==="
npx --no-install snarkjs groth16 verify \
  build/engram_post_generated_verification_key.json \
  build/public_real_file.json \
  build/proof_real_file.json

echo "=== END-TO-END CHECK COMPLETED ==="
echo "For repeated baseline measurements, run from zk/:"
echo "  python3 benchmark_groth16.py --input input_real_file.json --source-size-bytes $(python3 -c 'import json; print(json.load(open("input_real_file_metadata.json"))["sourceSizeBytes"])') --repeat 30 --warmup 2"
