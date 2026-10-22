
#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "Usage: bash run_real_file_post.sh <path-to-input-file>"
    exit 1
fi

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

echo "=== 1. Prepare real file, shards, Merkle tree and challenge ==="
PYTHONPATH=src python3 examples/test_real_file_post.py "$1"

MAX_ZK_SHARD_BYTES="${MAX_ZK_SHARD_BYTES:-61}"
SHARD_SIZE="$(python3 -c 'import json; print(json.load(open("zk/test_real_file_metadata.json", encoding="utf-8"))["shard_bytes_len"])')"
if (( SHARD_SIZE > MAX_ZK_SHARD_BYTES )); then
    echo "[STOP] Shard size is ${SHARD_SIZE} bytes, above the current safe limit ${MAX_ZK_SHARD_BYTES} bytes."
    echo "The current circuit hashes every 31-byte chunk and uses pot14_final.ptau."
    echo "Use the Python benchmark for large files:"
    echo "  PYTHONPATH=src python -m engram.experiment \"$1\" --repeat 3"
    echo "If you change the circuit/setup capacity, set MAX_ZK_SHARD_BYTES explicitly."
    exit 2
fi

cd "$ROOT/zk"

echo "=== 2. Compile circuit for the actual shard size ==="
circom circuits/engram_post_real_file.circom \
    --r1cs --wasm --sym \
    -l node_modules \
    -o build

echo "=== 3. Groth16 setup ==="
npx snarkjs groth16 setup \
    build/engram_post_real_file.r1cs \
    build/pot14_final.ptau \
    build/engram_post_real_file_final.zkey

echo "=== 4. Export verification key ==="
npx snarkjs zkey export verificationkey \
    build/engram_post_real_file_final.zkey \
    build/engram_post_real_file_verification_key.json

echo "=== 5. Generate witness ==="
node build/engram_post_real_file_js/generate_witness.js \
    build/engram_post_real_file_js/engram_post_real_file.wasm \
    input_real_file.json \
    build/witness_real_file.wtns

echo "=== 6. Generate Groth16 proof ==="
npx snarkjs groth16 prove \
    build/engram_post_real_file_final.zkey \
    build/witness_real_file.wtns \
    build/proof_real_file.json \
    build/public_real_file.json

echo "=== 7. Verify proof ==="
npx snarkjs groth16 verify \
    build/engram_post_real_file_verification_key.json \
    build/public_real_file.json \
    build/proof_real_file.json

echo "=== 8. Negative test: tamper with a shard chunk ==="
python3 - <<'PY'
import json

with open("input_real_file.json", encoding="utf-8") as f:
    data = json.load(f)

value = int(data["chunkValues"][0][0])
data["chunkValues"][0][0] = str(value ^ 1)

with open("input_real_file_tampered.json", "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)

print("Created input_real_file_tampered.json")
PY

if node build/engram_post_real_file_js/generate_witness.js \
    build/engram_post_real_file_js/engram_post_real_file.wasm \
    input_real_file_tampered.json \
    build/witness_real_file_tampered.wtns
then
    echo "[FAIL] Witness calculator accepted tampered input."
    exit 1
else
    echo "[PASS] Tampered input was rejected."
fi

echo "=== END-TO-END TEST COMPLETED ==="
