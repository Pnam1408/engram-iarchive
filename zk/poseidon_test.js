const { buildPoseidon } = require("circomlibjs");

async function main() {
    const poseidon = await buildPoseidon();

    // Hash một input
    const hash1 = poseidon([1n]);

    console.log("Poseidon([1]):");
    console.log(poseidon.F.toString(hash1));

    // Hash hai inputs
    const hash2 = poseidon([1n, 2n]);

    console.log("\nPoseidon([1, 2]):");
    console.log(poseidon.F.toString(hash2));

    // Hash ba inputs
    const hash3 = poseidon([11n, 22n, 2026n]);

    console.log("\nPoseidon([11, 22, 2026]):");
    console.log(poseidon.F.toString(hash3));
}

main();