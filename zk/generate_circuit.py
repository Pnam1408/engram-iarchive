
import argparse
import json
import math
from pathlib import Path


ZK_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = ZK_DIR / "input.json"
DEFAULT_TEMPLATE = ZK_DIR / "circuits" / "engram_post_template.circom"
DEFAULT_OUTPUT = ZK_DIR / "circuits" / "engram_post_generated.circom"


def get_circuit_params(
    shard_bytes_len: int,
    n_shards: int = 8,
    n_selected: int = 3,
) -> dict[str, int]:
    """Calculate compile-time parameters for EngramPoSt."""

    if shard_bytes_len <= 31:
        raise ValueError(
            "This circuit requires shard_bytes_len > 31 "
            "so each shard has at least two chunks."
        )

    if n_shards < 2 or n_shards & (n_shards - 1):
        raise ValueError(
            "n_shards must be a power of two and at least 2."
        )

    if n_selected != 3:
        raise ValueError("The current circuit supports exactly 3 selected shards.")

    n_chunks = math.ceil(shard_bytes_len / 31)
    chunk_depth = math.ceil(math.log2(n_chunks))
    shard_depth = int(math.log2(n_shards))

    return {
        "n_shards": n_shards,
        "n_selected": n_selected,
        "shard_bytes_len": shard_bytes_len,
        "n_chunks": n_chunks,
        "chunk_depth": chunk_depth,
        "shard_depth": shard_depth,
    }


def load_input_metadata(
    input_path: Path,
    shard_bytes_arg: int | None,
    n_shards_arg: int | None,
) -> tuple[int, int]:
    data = json.loads(input_path.read_text(encoding="utf-8"))
    metadata_path = input_path.with_name(f"{input_path.stem}_metadata.json")
    metadata = {}
    if metadata_path.is_file():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    # Backward-compatible fallback for older input files; new files keep
    # compile-time dimensions in a sidecar so witness input has signals only.
    shard_bytes_len = shard_bytes_arg or metadata.get("shardBytesLen") or data.get("shardBytesLen")
    n_shards = n_shards_arg or metadata.get("nShards") or data.get("nShards", 8)

    if shard_bytes_len is None:
        raise ValueError(
            "Missing shard size. Add 'shardBytesLen' to input.json "
            "or pass --shard-bytes-len."
        )

    if not isinstance(shard_bytes_len, int) or shard_bytes_len <= 0:
        raise ValueError("'shardBytesLen' must be a positive integer.")

    if not isinstance(n_shards, int) or n_shards <= 0:
        raise ValueError("'nShards' must be a positive integer.")

    # Check that the witness arrays match the circuit dimensions.
    for key in ("chunkValues", "chunkLengths", "chunkPaths", "shardPaths"):
        if key not in data:
            raise ValueError(f"input.json is missing required field: {key}")

    if len(data["chunkValues"]) != 3:
        raise ValueError("input.json must contain exactly 3 chunk witnesses.")

    if len(data["chunkLengths"]) != 3:
        raise ValueError("input.json must contain exactly 3 chunk lengths.")

    if len(data["chunkPaths"]) != 3 or len(data["shardPaths"]) != 3:
        raise ValueError("input.json must contain 3 paths of each type.")

    params = get_circuit_params(shard_bytes_len, n_shards)

    expected_chunks = params["n_chunks"]
    expected_chunk_depth = params["chunk_depth"]
    expected_shard_depth = params["shard_depth"]

    if any(len(path) != expected_chunk_depth for path in data["chunkPaths"]):
        raise ValueError(
            f"Chunk path depth must be {expected_chunk_depth} "
            f"for {expected_chunks} chunks per shard."
        )

    if any(len(path) != expected_shard_depth for path in data["shardPaths"]):
        raise ValueError(
            f"Shard path depth must be {expected_shard_depth} "
            f"for {n_shards} shards."
        )

    return shard_bytes_len, n_shards


def generate_circuit(
    template_path: Path,
    output_path: Path,
    shard_bytes_len: int,
    n_shards: int,
) -> dict[str, int]:
    if not template_path.is_file():
        raise FileNotFoundError(f"Circuit template not found: {template_path}")

    params = get_circuit_params(shard_bytes_len, n_shards)

    # The generated file is placed beside the template so that its
    # relative include works regardless of the current working directory.
    output = f'''pragma circom 2.0.0;

include "engram_post_template.circom";

component main {{
    public [
        merkleRoot,
        idx0, idx1, idx2,
        chunkIdx0, chunkIdx1, chunkIdx2
    ]
}} = EngramPoSt(
    {params["n_shards"]},
    {params["n_selected"]},
    {params["shard_bytes_len"]},
    {params["n_chunks"]},
    {params["chunk_depth"]},
    {params["shard_depth"]}
);
'''

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(output, encoding="utf-8")

    return params


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate an EngramPoSt Circom main file from input.json."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--shard-bytes-len",
        type=int,
        default=None,
        help="Override shard size in bytes; otherwise read shardBytesLen from JSON.",
    )
    parser.add_argument(
        "--n-shards",
        type=int,
        default=None,
        help="Override shard count; otherwise read nShards from JSON (default 8).",
    )
    args = parser.parse_args()

    input_path = args.input.resolve()
    template_path = args.template.resolve()
    output_path = args.output.resolve()

    if not input_path.is_file():
        raise FileNotFoundError(f"Input JSON not found: {input_path}")

    shard_bytes_len, n_shards = load_input_metadata(
        input_path,
        args.shard_bytes_len,
        args.n_shards,
    )

    params = generate_circuit(
        template_path,
        output_path,
        shard_bytes_len,
        n_shards,
    )

    print(f"Generated circuit: {output_path}")
    print("Circuit parameters:")
    for name, value in params.items():
        print(f"  {name}: {value}")


if __name__ == "__main__":
    main()
