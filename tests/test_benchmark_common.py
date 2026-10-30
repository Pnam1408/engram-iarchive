import unittest

from benchmarks.common.workload import (
    build_snapshots, deterministic_bytes, deterministic_payload,
    generate_update_workload,
)
from benchmarks.common.statistics import (
    median_confidence_interval, numeric_summary, percentile_linear,
    percentile_nearest_rank, summarize_pipeline_rows,
)


class WorkloadTests(unittest.TestCase):
    def test_legacy_payload_is_deterministic(self):
        self.assertEqual(deterministic_payload(100), deterministic_payload(100))
        self.assertEqual(deterministic_payload(0), b"")
        self.assertEqual(len(deterministic_payload(1024)), 1024)

    def test_legacy_seeded_bytes_are_deterministic(self):
        self.assertEqual(deterministic_bytes(100, 42), deterministic_bytes(100, 42))
        self.assertNotEqual(deterministic_bytes(100, 42), deterministic_bytes(100, 43))

    def test_update_workload_sizes(self):
        workload = generate_update_workload(10, 3, 2, seed=42)
        self.assertEqual(len(workload.initial_data), 10)
        self.assertEqual(workload.delta_sizes_bytes, (3, 3))
        self.assertEqual(workload.final_size_bytes, 16)

    def test_snapshots_preserve_version_and_delta_semantics(self):
        snapshots = build_snapshots(10, 3, 2, seed=42)
        self.assertEqual([s[0] for s in snapshots], [0, 1, 2])
        self.assertEqual([s[1] for s in snapshots], [0, 3, 3])
        self.assertEqual([len(s[2]) for s in snapshots], [10, 13, 16])
        self.assertEqual(snapshots[1][2], snapshots[0][2] + snapshots[1][2][10:])


class StatisticsTests(unittest.TestCase):
    def test_nearest_rank_percentile(self):
        self.assertEqual(percentile_nearest_rank([1, 2, 3, 4], 95), 4)

    def test_linear_percentile(self):
        self.assertAlmostEqual(percentile_linear([1, 2, 3, 4], 0.5), 2.5)
        self.assertIsNone(percentile_linear([], 0.95))

    def test_median_ci_is_deterministic(self):
        self.assertEqual(
            median_confidence_interval([1, 2, 3, 4, 5], seed=123),
            median_confidence_interval([1, 2, 3, 4, 5], seed=123),
        )

    def test_numeric_summary_ignores_booleans(self):
        result = numeric_summary([
            {"latency_ms": 1.0, "passed": True},
            {"latency_ms": 3.0, "passed": False},
        ])
        self.assertEqual(result["latency_ms"]["median"], 2.0)
        self.assertNotIn("passed", result)

    def test_pipeline_summary_schema(self):
        rows = [
            {"source_file": "a.bin", "run_type": "measurement", "repeat": 1,
             "pipeline_end_to_end_ms": 10.0, "reconstruction_success": True,
             "python_merkle_proof_success": True},
            {"source_file": "a.bin", "run_type": "measurement", "repeat": 2,
             "pipeline_end_to_end_ms": 12.0, "reconstruction_success": True,
             "python_merkle_proof_success": True},
        ]
        summary = summarize_pipeline_rows(rows)
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]["pipeline_end_to_end_ms_median"], 11.0)
        self.assertTrue(summary[0]["reconstruction_success_all_passed"])


if __name__ == "__main__":
    unittest.main()
