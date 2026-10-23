#!/usr/bin/env python3
"""Run the baseline matrix: 1 KiB/64 KiB/1 MiB × RS(4,8)/RS(6,8).

From repository root:
  PYTHONPATH=src python scripts/run_baseline_matrix.py --repeat 30 --warmup 2
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKLOAD_DIR = ROOT / "data" / "benchmark_workloads"


def deterministic_payload(size: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < size:
        out.extend(hashlib.sha256(f"engram-baseline-v1:{counter}".encode()).digest())
        counter += 1
    return bytes(out[:size])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--output-dir", type=Path, default=Path("results/pipeline"))
    args = parser.parse_args()
    if args.repeat < 1 or args.warmup < 0:
        parser.error("--repeat must be >= 1 and --warmup must be >= 0")

    WORKLOAD_DIR.mkdir(parents=True, exist_ok=True)
    workloads = []
    for label, size in (("1KiB", 1024), ("64KiB", 64 * 1024), ("1MiB", 1024 * 1024)):
        path = WORKLOAD_DIR / f"baseline_{label}.txt"
        payload = deterministic_payload(size)
        path.write_bytes(payload)
        workloads.append(path)
        print(f"Prepared {path.relative_to(ROOT)} ({size} bytes; sha256={hashlib.sha256(payload).hexdigest()})")

    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(ROOT / "src") + (os.pathsep + existing_pythonpath if existing_pythonpath else "")

    for k in (4, 6):
        output_dir = args.output_dir / f"rs{k}_8"
        command = [
            sys.executable, "-m", "engram.experiment",
            *[str(p) for p in workloads],
            "--repeat", str(args.repeat),
            "--warmup", str(args.warmup),
            "--data-shards", str(k),
            "--total-shards", "8",
            "--challenge-size", "3",
            "--output-dir", str(output_dir),
        ]
        print("\n=== Running RS(%d,8) ===" % k, flush=True)
        subprocess.run(command, cwd=ROOT, env=env, check=True)

    print("\nBaseline matrix completed.")
    print(f"Raw CSV and summaries are under: {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
