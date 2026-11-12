# zk/generate_circuit.py

from __future__ import annotations

import argparse
import json
import math
import os
import re
from pathlib import Path
from typing import Any

ZK_DIR = Path(__file__).resolve().parent

DEFAULT_INPUT = ZK_DIR / "input.json"
DEFAULT_TEMPLATE = ZK_DIR / "circuits" / "engram_shard_post_template.circom"
DEFAULT_OUTPUT = ZK_DIR / "circuits" / "engram_post_generated.circom"

# BN254 scalar field modulus, matching merkle.py.
FIELD_MODULUS = (
    21888242871839275222246405745257275088548364400416034343698204186575808495617
)


def _parse_nonnegative_int(
    value: Any,
    field_name: str,
) -> int:
    """Parse a non-negative integer from JSON."""

    if isinstance(value, bool):
        raise TypeError(f"{field_name} must be an integer")

    if isinstance(value, int):
        result = value
    elif isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        result = int(value)
    else:
        raise ValueError(f"{field_name} must be a non-negative integer")

    if result < 0:
        raise ValueError(f"{field_name} must be non-negative")

    return result


def get_circuit_params(
    shard_bytes_len: int,
    n_shards: int = 8,
) -> dict[str, int]:
    """Calculate compile-time parameters for EngramShardPoSt."""

    if not isinstance(shard_bytes_len, int) or isinstance(shard_bytes_len, bool):
        raise TypeError("shard_bytes_len must be an integer")

    if shard_bytes_len <= 31:
        raise ValueError(
            "This circuit requires shard_bytes_len > 31 "
            "so each shard has at least two chunks."
        )

    if not isinstance(n_shards, int) or isinstance(n_shards, bool):
        raise TypeError("n_shards must be an integer")

    if n_shards < 2 or n_shards & (n_shards - 1):
        raise ValueError("n_shards must be a power of two and at least 2.")

    n_chunks = math.ceil(shard_bytes_len / 31)

    # Equivalent to ceil(log2(n_chunks)), without floating-point errors.
    chunk_depth = (n_chunks - 1).bit_length()

    shard_depth = n_shards.bit_length() - 1

    return {
        "n_shards": n_shards,
        "shard_bytes_len": shard_bytes_len,
        "n_chunks": n_chunks,
        "chunk_depth": chunk_depth,
        "shard_depth": shard_depth,
    }


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON file: {path}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object in {path}")

    return data


def _validate_field_element(
    value: Any,
    field_name: str,
) -> int:
    result = _parse_nonnegative_int(value, field_name)

    if result >= FIELD_MODULUS:
        raise ValueError(f"{field_name} must be smaller than the BN254 field modulus")

    return result


def _validate_input(
    data: dict[str, Any],
    shard_bytes_len: int,
    n_shards: int,
) -> None:
    """Validate single-target witness input against circuit dimensions."""

    required_fields = (
        "merkleRoot",
        "shardIndex",
        "chunkIndex",
        "chunkValue",
        "chunkLength",
        "chunkPath",
        "shardPath",
    )

    for key in required_fields:
        if key not in data:
            raise ValueError(f"input.json is missing required field: {key}")

    params = get_circuit_params(shard_bytes_len, n_shards)

    n_chunks = params["n_chunks"]
    chunk_depth = params["chunk_depth"]
    shard_depth = params["shard_depth"]

    # Validate public inputs.
    _validate_field_element(data["merkleRoot"], "merkleRoot")

    shard_index = _parse_nonnegative_int(
        data["shardIndex"],
        "shardIndex",
    )

    chunk_index = _parse_nonnegative_int(
        data["chunkIndex"],
        "chunkIndex",
    )

    if shard_index >= n_shards:
        raise ValueError(f"shardIndex must be smaller than {n_shards}")

    if chunk_index >= n_chunks:
        raise ValueError(f"chunkIndex must be smaller than {n_chunks}")

    # Validate private chunk witness.
    chunk_value = _parse_nonnegative_int(
        data["chunkValue"],
        "chunkValue",
    )

    chunk_length = _parse_nonnegative_int(
        data["chunkLength"],
        "chunkLength",
    )

    last_chunk_bytes = shard_bytes_len % 31 or 31

    expected_chunk_length = last_chunk_bytes if chunk_index == n_chunks - 1 else 31

    if chunk_length != expected_chunk_length:
        raise ValueError(
            f"chunkLength must be {expected_chunk_length} "
            f"for chunkIndex {chunk_index}, got {chunk_length}"
        )

    if chunk_value >= 2**248:
        raise ValueError("chunkValue must fit in 248 bits")

    if chunk_value >= 2 ** (chunk_length * 8):
        raise ValueError("chunkValue contains more bytes than chunkLength allows")

    # Validate Merkle paths.
    chunk_path = data["chunkPath"]
    shard_path = data["shardPath"]

    if not isinstance(chunk_path, list):
        raise TypeError("chunkPath must be a JSON array")

    if not isinstance(shard_path, list):
        raise TypeError("shardPath must be a JSON array")

    if len(chunk_path) != chunk_depth:
        raise ValueError(
            f"chunkPath depth must be {chunk_depth}, " f"got {len(chunk_path)}"
        )

    if len(shard_path) != shard_depth:
        raise ValueError(
            f"shardPath depth must be {shard_depth}, " f"got {len(shard_path)}"
        )

    for index, sibling in enumerate(chunk_path):
        _validate_field_element(
            sibling,
            f"chunkPath[{index}]",
        )

    for index, sibling in enumerate(shard_path):
        _validate_field_element(
            sibling,
            f"shardPath[{index}]",
        )


def load_input_metadata(
    input_path: Path,
    shard_bytes_arg: int | None,
    n_shards_arg: int | None,
) -> tuple[int, int]:
    """Load circuit dimensions and validate single-target input.

    Dimensions are read from:
      1. CLI arguments, if provided.
      2. The input metadata sidecar.
      3. Optional legacy fields in input.json.
    """

    data = _load_json_object(input_path)

    metadata_path = input_path.with_name(f"{input_path.stem}_metadata.json")

    metadata: dict[str, Any] = {}

    if metadata_path.is_file():
        metadata = _load_json_object(metadata_path)

    shard_bytes_len = (
        shard_bytes_arg
        if shard_bytes_arg is not None
        else metadata.get("shardBytesLen", data.get("shardBytesLen"))
    )

    n_shards = (
        n_shards_arg
        if n_shards_arg is not None
        else metadata.get("nShards", data.get("nShards", 8))
    )

    if shard_bytes_len is None:
        raise ValueError(
            "Missing shard size. Add 'shardBytesLen' to the metadata "
            "JSON or pass --shard-bytes-len."
        )

    if (
        not isinstance(shard_bytes_len, int)
        or isinstance(shard_bytes_len, bool)
        or shard_bytes_len <= 0
    ):
        raise ValueError("'shardBytesLen' must be a positive integer.")

    if not isinstance(n_shards, int) or isinstance(n_shards, bool) or n_shards <= 0:
        raise ValueError("'nShards' must be a positive integer.")

    _validate_input(
        data=data,
        shard_bytes_len=shard_bytes_len,
        n_shards=n_shards,
    )

    return shard_bytes_len, n_shards


def generate_circuit(
    template_path: Path,
    output_path: Path,
    shard_bytes_len: int,
    n_shards: int,
) -> dict[str, int]:
    """Generate a single-target Circom main component."""

    if not template_path.is_file():
        raise FileNotFoundError(f"Circuit template not found: {template_path}")

    params = get_circuit_params(
        shard_bytes_len=shard_bytes_len,
        n_shards=n_shards,
    )

    # Make the include path relative to the generated circuit.
    include_path = os.path.relpath(
        template_path.resolve(),
        output_path.resolve().parent,
    ).replace(os.sep, "/")

    output = f"""pragma circom 2.0.0;

include "{include_path}";

component main {{
    public [
        merkleRoot,
        shardIndex,
        chunkIndex
    ]
}} = EngramShardPoSt(
    {params["n_shards"]},
    {params["shard_bytes_len"]},
    {params["n_chunks"]},
    {params["chunk_depth"]},
    {params["shard_depth"]}
);
"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(output, encoding="utf-8")

    return params


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a single-target EngramShardPoSt Circom "
            "main file from input.json."
        )
    )

    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
    )

    parser.add_argument(
        "--template",
        type=Path,
        default=DEFAULT_TEMPLATE,
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
    )

    parser.add_argument(
        "--shard-bytes-len",
        type=int,
        default=None,
        help=(
            "Override shard size in bytes; otherwise read "
            "shardBytesLen from metadata."
        ),
    )

    parser.add_argument(
        "--n-shards",
        type=int,
        default=None,
        help=(
            "Override shard count; otherwise read nShards " "from metadata (default 8)."
        ),
    )

    args = parser.parse_args()

    input_path = args.input.resolve()
    template_path = args.template.resolve()
    output_path = args.output.resolve()

    if not input_path.is_file():
        raise FileNotFoundError(f"Input JSON not found: {input_path}")

    shard_bytes_len, n_shards = load_input_metadata(
        input_path=input_path,
        shard_bytes_arg=args.shard_bytes_len,
        n_shards_arg=args.n_shards,
    )

    params = generate_circuit(
        template_path=template_path,
        output_path=output_path,
        shard_bytes_len=shard_bytes_len,
        n_shards=n_shards,
    )

    print(f"Generated circuit: {output_path}")
    print("Circuit parameters:")

    for name, value in params.items():
        print(f"  {name}: {value}")


if __name__ == "__main__":
    main()
