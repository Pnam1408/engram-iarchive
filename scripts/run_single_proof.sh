
#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PTAU_PATH="${PTAU_PATH:-$ROOT/zk/build/powersOfTau28_hez_final_16.ptau}"

if [[ ! -f "$PTAU_PATH" ]]; then
    echo "Missing Powers of Tau file: $PTAU_PATH" >&2
    echo "Set PTAU_PATH to an existing Powers of Tau file." >&2
    exit 2
fi

echo "=== 1. Prepare workload and single-chunk input ==="

PREPARE_OUTPUT="$(
    PYTHONPATH=src python3 scripts/smoke_test_single_proof.py
)"

printf '%s\n' "$PREPARE_OUTPUT"

CIRCUIT_ABS="$(
    printf '%s\n' "$PREPARE_OUTPUT" |
    sed -n 's/^CIRCUIT_OUTPUT=//p' |
    tail -n 1
)"

if [[ -z "$CIRCUIT_ABS" || ! -f "$CIRCUIT_ABS" ]]; then
    echo "Generated circuit file not found: $CIRCUIT_ABS" >&2
    exit 3
fi

CIRCUIT_REL="${CIRCUIT_ABS#"$ROOT/zk/"}"

if [[ "$CIRCUIT_REL" == "$CIRCUIT_ABS" ]]; then
    echo "Circuit must be generated inside the zk directory." >&2
    exit 3
fi

CIRCUIT_NAME="$(basename "$CIRCUIT_REL" .circom)"
INPUT_FILE="build/smoke_single/input.json"
OUTPUT_DIR="build/smoke_single"

mkdir -p "$ROOT/zk/build"
cd "$ROOT/zk"

echo
echo "=== 2. Compile Circom circuit ==="

circom "$CIRCUIT_REL" \
    --r1cs \
    --wasm \
    --sym \
    -l node_modules \
    -o build

R1CS_FILE="build/${CIRCUIT_NAME}.r1cs"
WASM_DIR="build/${CIRCUIT_NAME}_js"
WASM_FILE="${WASM_DIR}/${CIRCUIT_NAME}.wasm"
WITNESS_JS="${WASM_DIR}/generate_witness.js"

if [[ ! -f "$R1CS_FILE" || ! -f "$WASM_FILE" ]]; then
    echo "Circuit compilation outputs are incomplete." >&2
    exit 4
fi

echo
echo "=== 3. Groth16 setup ==="

npx --no-install snarkjs groth16 setup \
    "$R1CS_FILE" \
    "$PTAU_PATH" \
    "${OUTPUT_DIR}/circuit.zkey"

npx --no-install snarkjs zkey export verificationkey \
    "${OUTPUT_DIR}/circuit.zkey" \
    "${OUTPUT_DIR}/verification_key.json"

echo
echo "=== 4. Generate witness ==="

node "$WITNESS_JS" \
    "$WASM_FILE" \
    "$INPUT_FILE" \
    "${OUTPUT_DIR}/witness.wtns"

echo
echo "=== 5. Generate Groth16 proof ==="

npx --no-install snarkjs groth16 prove \
    "${OUTPUT_DIR}/circuit.zkey" \
    "${OUTPUT_DIR}/witness.wtns" \
    "${OUTPUT_DIR}/proof.json" \
    "${OUTPUT_DIR}/public.json"

echo
echo "=== 6. Verify Groth16 proof ==="

npx --no-install snarkjs groth16 verify \
    "${OUTPUT_DIR}/verification_key.json" \
    "${OUTPUT_DIR}/public.json" \
    "${OUTPUT_DIR}/proof.json"

echo
echo "=== SINGLE-CHUNK GROTH16 TEST COMPLETED ==="
echo "Input:          $INPUT_FILE"
echo "Proof:          ${OUTPUT_DIR}/proof.json"
echo "Public inputs:  ${OUTPUT_DIR}/public.json"
