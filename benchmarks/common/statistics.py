#benchmarks/common/statistics.py
"""Shared statistical helpers for ENGRAM benchmark results.

The current scripts use two percentile conventions. Both are preserved under
explicit names so refactoring does not silently change reported p95 values.
"""
from __future__ import annotations

import math
import random
import statistics as _statistics
from typing import Any, Mapping, Sequence

BOOTSTRAP_SAMPLES = 2000


def percentile_nearest_rank(values: Sequence[float], p: float) -> float:
    """Nearest-rank percentile; p is on a 0..100 scale.

    Matches the current engram.experiment.percentile implementation.
    """
    if not values:
        raise ValueError("values must not be empty")
    if not 0 <= p <= 100:
        raise ValueError("p must be between 0 and 100")
    ordered = sorted(values)
    rank = max(1, min(len(ordered), int((p / 100.0) * len(ordered) + 0.999999)))
    return ordered[rank - 1]


def percentile_linear(values: Sequence[float], p: float) -> float | None:
    """Linearly interpolated percentile; p is a fraction in [0, 1].

    Matches the current benchmark_full_snapshot.py implementation and returns
    None for empty input.
    """
    if not values:
        return None
    if not 0 <= p <= 1:
        raise ValueError("p must be between 0 and 1")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * p
    lo, hi = math.floor(rank), math.ceil(rank)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (rank - lo)


def median_confidence_interval(
    values: Sequence[float],
    seed: int,
    samples: int = BOOTSTRAP_SAMPLES,
) -> tuple[float, float]:
    """Bootstrap percentile 95% CI for the median, matching baseline logic."""
    if not values:
        raise ValueError("values must not be empty")
    if samples < 1:
        raise ValueError("samples must be >= 1")
    if len(values) == 1:
        return values[0], values[0]
    rng = random.Random(seed)
    boot_medians = [
        _statistics.median(rng.choices(values, k=len(values)))
        for _ in range(samples)
    ]
    return (
        percentile_nearest_rank(boot_medians, 2.5),
        percentile_nearest_rank(boot_medians, 97.5),
    )


def numeric_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarize numeric columns, matching the full-snapshot schema."""
    keys = sorted({
        key for row in rows for key, value in row.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    })
    summary: dict[str, Any] = {}
    for key in keys:
        values = [
            float(row[key]) for row in rows
            if isinstance(row.get(key), (int, float))
            and not isinstance(row.get(key), bool)
        ]
        if values:
            summary[key] = {
                "n": len(values),
                "median": _statistics.median(values),
                "p95": percentile_linear(values, 0.95),
                "min": min(values),
                "max": max(values),
            }
    return summary


def summarize_pipeline_rows(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Summarize baseline rows grouped by source_file and run_type.

    Preserves field names, nearest-rank p95, and bootstrap CI behavior from
    engram.experiment.summarize.
    """
    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for row in rows:
        key = (str(row["source_file"]), str(row["run_type"]))
        groups.setdefault(key, []).append(row)

    output: list[dict[str, Any]] = []
    for group_index, ((source_file, run_type), group) in enumerate(groups.items()):
        item: dict[str, Any] = {
            "source_file": source_file, "run_type": run_type, "runs": len(group)
        }
        keys = sorted({
            key for row in group for key, value in row.items()
            if isinstance(value, (int, float))
            and not isinstance(value, bool) and key != "repeat"
        })
        for i, key in enumerate(keys):
            values = [
                float(row[key]) for row in group
                if isinstance(row.get(key), (int, float))
                and not isinstance(row.get(key), bool)
            ]
            if not values:
                continue
            item[f"{key}_median"] = _statistics.median(values)
            item[f"{key}_p95"] = percentile_nearest_rank(values, 95)
            item[f"{key}_min"] = min(values)
            item[f"{key}_max"] = max(values)
            lo, hi = median_confidence_interval(
                values, seed=20261022 + group_index * 1000 + i
            )
            item[f"{key}_median_ci95_low"] = lo
            item[f"{key}_median_ci95_high"] = hi
        for key in ("reconstruction_success", "python_merkle_proof_success"):
            item[f"{key}_all_passed"] = all(row.get(key) is True for row in group)
        output.append(item)
    return output
