
import csv
import json

import pytest

from benchmarks.common.results import (
    create_result_stem,
    export_results,
    write_csv,
    write_json,
)


def test_create_result_stem_contains_prefix():
    assert create_result_stem("pipeline_baseline").startswith(
        "pipeline_baseline_"
    )


def test_write_csv_preserves_explicit_column_order(tmp_path):
    path = tmp_path / "nested" / "runs.csv"
    rows = [{"b": 2, "a": 1}]

    write_csv(path, rows, fieldnames=["b", "a"])

    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        assert reader.fieldnames == ["b", "a"]
        assert next(reader) == {"b": "2", "a": "1"}


def test_write_csv_sorts_columns_by_default(tmp_path):
    path = tmp_path / "runs.csv"
    write_csv(path, [{"b": 2, "a": 1}])

    with path.open(encoding="utf-8-sig", newline="") as file:
        assert csv.DictReader(file).fieldnames == ["a", "b"]


def test_export_results_includes_raw_csv_path(tmp_path):
    exported = export_results(
        output_dir=tmp_path / "results",
        prefix="pipeline_baseline",
        rows=[{"run_id": "abc", "elapsed_ms": 1.2}],
        summary={"summary": [{"runs": 1}]},
    )

    assert exported.csv_path.is_file()
    assert exported.json_path.is_file()

    data = json.loads(exported.json_path.read_text(encoding="utf-8"))
    assert data["raw_csv"] == str(exported.csv_path)
    assert data["summary"] == [{"runs": 1}]


def test_export_results_rejects_empty_rows(tmp_path):
    with pytest.raises(ValueError):
        export_results(
            output_dir=tmp_path,
            prefix="empty",
            rows=[],
            summary={},
        )


def test_write_json_preserves_unicode(tmp_path):
    path = tmp_path / "summary.json"
    write_json(path, {"description": "Kết quả thí nghiệm"})

    content = path.read_text(encoding="utf-8")
    assert "Kết quả thí nghiệm" in content
    assert json.loads(content)["description"] == "Kết quả thí nghiệm"
