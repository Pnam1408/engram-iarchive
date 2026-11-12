from __future__ import annotations

import unittest

from benchmarks.common.workload import generate_insert_delete_workload


class InsertDeleteWorkloadTests(unittest.TestCase):
    def test_workload_is_deterministic_and_reconstructible(self) -> None:
        first = generate_insert_delete_workload(
            4096, 128, 96, 10, seed=7, pattern="mixed"
        )
        second = generate_insert_delete_workload(
            4096, 128, 96, 10, seed=7, pattern="mixed"
        )
        self.assertEqual(first, second)
        self.assertEqual(len(first.snapshots), 11)
        self.assertEqual(first.snapshots[0], first.initial_data)

        current = bytearray(first.initial_data)
        for operation, expected in zip(first.operations, first.snapshots[1:]):
            if operation.operation == "insert":
                current[operation.offset:operation.offset] = operation.payload
            else:
                current[operation.offset:operation.offset + operation.delete_length] = b""
            self.assertEqual(bytes(current), expected)

    def test_all_position_patterns_generate_valid_offsets(self) -> None:
        for pattern in ("mixed", "beginning", "middle", "end", "distributed", "localized"):
            workload = generate_insert_delete_workload(
                2048, 64, 48, 8, seed=11, pattern=pattern
            )
            for operation, before in zip(workload.operations, workload.snapshots):
                self.assertGreaterEqual(operation.offset, 0)
                self.assertLessEqual(operation.offset, len(before))
                if operation.operation == "delete":
                    self.assertGreater(operation.delete_length, 0)
                    self.assertLessEqual(
                        operation.offset + operation.delete_length, len(before)
                    )
                    self.assertGreaterEqual(len(before) - operation.delete_length, 1)
                else:
                    self.assertTrue(operation.payload)

    def test_invalid_parameters_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            generate_insert_delete_workload(0, 10, 10, 4)
        with self.assertRaises(ValueError):
            generate_insert_delete_workload(100, 10, 10, 0)
        with self.assertRaises(ValueError):
            generate_insert_delete_workload(100, 10, 10, 4, pattern="unknown")


if __name__ == "__main__":
    unittest.main()
