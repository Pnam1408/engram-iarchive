# benchmarks/common/workload.py
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
        deterministic_bytes(delta_size, seed + i) for i in range(1, num_deltas + 1)
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


@dataclass(frozen=True)
class EditOperation:
    """One deterministic byte-range edit against the preceding version."""

    index: int
    operation: str
    offset: int
    payload: bytes = b""
    delete_length: int = 0

    @property
    def payload_size_bytes(self) -> int:
        return len(self.payload)


@dataclass(frozen=True)
class InsertDeleteWorkload:
    """Initial object, ordered edits, and expected immutable snapshots."""

    initial_data: bytes
    operations: tuple[EditOperation, ...]
    snapshots: tuple[bytes, ...]
    seed: int
    pattern: str

    @property
    def final_size_bytes(self) -> int:
        return len(self.snapshots[-1])

    @property
    def total_inserted_bytes(self) -> int:
        return sum(
            len(op.payload) for op in self.operations if op.operation == "insert"
        )

    @property
    def total_deleted_bytes(self) -> int:
        return sum(
            op.delete_length for op in self.operations if op.operation == "delete"
        )


def generate_insert_delete_workload(
    initial_size: int,
    insert_size: int,
    delete_size: int,
    operations: int,
    *,
    seed: int = 20261112,
    pattern: str = "mixed",
) -> InsertDeleteWorkload:
    """Build a deterministic Insert/Delete trace and its expected snapshots.

    ``pattern`` controls edit locations:
    - ``mixed`` cycles through beginning, middle, end, and deterministic
      distributed positions; useful as the default aggregate workload.
    - ``beginning``, ``middle``, ``end`` stress a fixed region.
    - ``distributed`` uses seeded positions across the current object.
    - ``localized`` repeatedly edits a narrow region around the midpoint.

    Operations alternate Insert and Delete, beginning with Insert. Delete
    lengths are clipped to preserve at least one byte in the object.
    Offsets are materialized against the exact preceding snapshot, so every
    chunker receives the same byte-for-byte operation trace.
    """
    _validate_positive_size(initial_size, "initial_size")
    _validate_positive_size(insert_size, "insert_size")
    _validate_positive_size(delete_size, "delete_size")
    if operations < 1:
        raise ValueError("operations must be >= 1")
    patterns = {"mixed", "beginning", "middle", "end", "distributed", "localized"}
    if pattern not in patterns:
        raise ValueError(f"pattern must be one of {sorted(patterns)}")
    if initial_size < 2:
        raise ValueError("initial_size must be >= 2 for insert/delete workloads")

    current = bytearray(deterministic_bytes(initial_size, seed))
    initial = bytes(current)
    snapshots: list[bytes] = [initial]
    edits: list[EditOperation] = []
    anchors = (0.0, 0.5, 1.0, 0.25, 0.75)

    for index in range(operations):
        is_insert = index % 2 == 0
        if pattern == "mixed":
            fraction = anchors[index % len(anchors)]
        elif pattern == "beginning":
            # Delete just after the inserted prefix rather than immediately
            # deleting the bytes that were inserted by the preceding version.
            fraction = 0.0 if is_insert else 0.10
        elif pattern == "middle":
            fraction = 0.50 if is_insert else 0.60
        elif pattern == "end":
            fraction = 1.0 if is_insert else 0.90
        elif pattern == "localized":
            # Keep both edit types in a narrow central band but offset their
            # positions so deletes do not simply undo the preceding insert.
            fraction = (
                (0.48 + (0.02 * ((index % 5) / 4)))
                if is_insert
                else (0.53 + (0.02 * ((index % 5) / 4)))
            )
        else:
            digest = hashlib.sha256(
                f"engram-edit-position-v1:{seed}:{index}".encode()
            ).digest()
            fraction = int.from_bytes(digest[:8], "big") / float(2**64 - 1)

        offset = min(len(current), int(fraction * len(current)))
        if is_insert:
            payload = deterministic_bytes(insert_size, seed + 10_000 + index)
            current[offset:offset] = payload
            edit = EditOperation(
                index=index,
                operation="insert",
                offset=offset,
                payload=payload,
            )
        else:
            # Preserve a non-empty object, avoiding degenerate later offsets.
            actual_length = min(delete_size, max(1, len(current) - 1))
            offset = min(offset, len(current) - actual_length)
            del current[offset : offset + actual_length]
            edit = EditOperation(
                index=index,
                operation="delete",
                offset=offset,
                delete_length=actual_length,
            )
        edits.append(edit)
        snapshots.append(bytes(current))

    return InsertDeleteWorkload(
        initial_data=initial,
        operations=tuple(edits),
        snapshots=tuple(snapshots),
        seed=seed,
        pattern=pattern,
    )
