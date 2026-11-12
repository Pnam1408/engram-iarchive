# benchmarks/common/results.py
"""Shared result-export utilities for ENGRAM benchmark scenarios."""

from __future__ import annotations

import csv
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class ExportedResults:
    """Paths of the result files produced by an export operation."""

    csv_path: Path
    json_path: Path


def create_result_stem(prefix: str) -> str:
    """Create a timestamped, collision-resistant result filename stem."""
    if not prefix or Path(prefix).name != prefix:
        raise ValueError("prefix must be a non-empty filename component")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = uuid.uuid4().hex[:6]
    return f"{prefix}_{timestamp}_{suffix}"


def write_csv(
    path: str | Path,
    rows: Sequence[Mapping[str, Any]],
    *,
    fieldnames: Sequence[str] | None = None,
    encoding: str = "utf-8-sig",
) -> Path:
    """Write dictionary rows to CSV.

    If fieldnames are provided, their order is preserved. Otherwise,
    columns are sorted across all rows, matching the full-snapshot
    benchmark's previous export behavior.
    """
    output_path = Path(path)

    if not rows:
        raise ValueError("Cannot write CSV: rows must not be empty")

    if fieldnames is None:
        columns = sorted({key for row in rows for key in row})
    else:
        columns = list(fieldnames)

    if not columns:
        raise ValueError("Cannot write CSV without columns")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open(
        "w",
        newline="",
        encoding=encoding,
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=columns,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)

    return output_path


def write_json(
    path: str | Path,
    data: Any,
    *,
    encoding: str = "utf-8",
) -> Path:
    """Write JSON using the formatting conventions of existing results."""
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding=encoding,
    )
    return output_path


def export_results(
    *,
    output_dir: str | Path,
    prefix: str,
    rows: Sequence[Mapping[str, Any]],
    summary: Mapping[str, Any],
    csv_fieldnames: Sequence[str] | None = None,
) -> ExportedResults:
    """Export raw CSV measurements and a JSON summary.

    The JSON output includes the raw CSV path under ``raw_csv``,
    preserving the baseline-pipeline summary schema.
    """
    if not rows:
        raise ValueError("Cannot export results: rows must not be empty")

    directory = Path(output_dir)
    stem = create_result_stem(prefix)

    csv_path = directory / f"{stem}_runs.csv"
    json_path = directory / f"{stem}_summary.json"

    write_csv(
        csv_path,
        rows,
        fieldnames=csv_fieldnames,
    )

    summary_data = dict(summary)
    summary_data["raw_csv"] = str(csv_path)

    write_json(json_path, summary_data)

    return ExportedResults(
        csv_path=csv_path,
        json_path=json_path,
    )
