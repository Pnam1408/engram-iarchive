#benchmarks/common/workload.py
"""Deterministic workload generators shared by ENGRAM benchmarks.

The two byte-generation algorithms intentionally preserve existing workloads:
- deterministic_payload: scripts/run_baseline_matrix.py
- deterministic_bytes: scripts/benchmark_full_snapshot.py

Do not merge/change these algorithms without versioning the workload, because
doing so changes input hashes and experimental inputs.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class UpdateWorkload:
    """Initial object plus a fixed sequence of append deltas."""
    initial_data: bytes
    deltas: tuple[bytes, ...]

    @property
    def initial_size_bytes(self) -> int:
        return len(self.initial_data)

    @property
    def delta_sizes_bytes(self) -> tuple[int, ...]:
        return tuple(len(delta) for delta in self.deltas)

    @property
    def final_size_bytes(self) -> int:
        return len(self.initial_data) + sum(map(len, self.deltas))


def deterministic_payload(size: int) -> bytes:
    """Generate the legacy baseline-matrix payload for ``size`` bytes."""
    _validate_nonnegative_size(size)
    out = bytearray()
    counter = 0
    while len(out) < size:
        out.extend(hashlib.sha256(f"engram-baseline-v1:{counter}".encode()).digest())
        counter += 1
    return bytes(out[:size])


def deterministic_bytes(size: int, seed: int) -> bytes:
    """Generate the legacy seeded byte stream used by the full-snapshot test."""
    _validate_nonnegative_size(size)
    out = bytearray()
    counter = 0
    seed_bytes = str(seed).encode("ascii")
    while len(out) < size:
        out.extend(hashlib.sha256(seed_bytes + counter.to_bytes(8, "big")).digest())
        counter += 1
    return bytes(out[:size])


def generate_update_workload(
    initial_size: int,
    delta_size: int,
    num_deltas: int,
    seed: int = 20261029,
) -> UpdateWorkload:
    """Generate reproducible initial bytes and independent deltas.

    The initial payload uses ``seed`` and delta i uses ``seed + i`` (i >= 1),
    matching the current B0 full-snapshot workload generator.
    """
    _validate_positive_size(initial_size, "initial_size")
    _validate_positive_size(delta_size, "delta_size")
    if num_deltas < 0:
        raise ValueError("num_deltas must be >= 0")
    initial = deterministic_bytes(initial_size, seed)
    deltas = tuple(
        deterministic_bytes(delta_size, seed + i)
        for i in range(1, num_deltas + 1)
    )
    return UpdateWorkload(initial_data=initial, deltas=deltas)


def build_snapshots(
    initial_size: int,
    delta_size: int,
    updates: int,
    seed: int,
) -> list[tuple[int, int, bytes]]:
    """Build B0 snapshots as (version, delta_size_bytes, full_payload).

    The return type and version-0 semantics match the existing
    scripts/benchmark_full_snapshot.py implementation.
    """
    workload = generate_update_workload(initial_size, delta_size, updates, seed)
    payload = workload.initial_data
    snapshots = [(0, 0, payload)]
    for version, delta in enumerate(workload.deltas, start=1):
        payload = payload + delta
        snapshots.append((version, len(delta), payload))
    return snapshots


def _validate_nonnegative_size(size: int) -> None:
    if size < 0:
        raise ValueError("size must be >= 0")


def _validate_positive_size(size: int, name: str) -> None:
    if size <= 0:
        raise ValueError(f"{name} must be > 0")
