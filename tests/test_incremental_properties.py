# tests/test_incremental_properties.py
import tempfile
import unittest

from hypothesis import given, settings, strategies as st

from engram.incremental import IncrementalStore

# Generate random operations:
# - append: append a non-empty delta
# - seal: explicitly seal the current generation
# - restart: simulate reopening the store
# - recover: explicitly trigger crash recovery
append_operation = st.tuples(
    st.just("append"),
    st.binary(min_size=1, max_size=16),
)

other_operations = st.tuples(
    st.sampled_from(("seal", "restart", "recover")),
    st.just(b""),
)

operation_sequences = st.lists(
    st.one_of(append_operation, other_operations),
    min_size=1,
    max_size=30,
)


class IncrementalPropertyTests(unittest.TestCase):

    def make_store(self, root):
        return IncrementalStore(
            root,
            data_shards=2,
            total_shards=4,
            chunk_size=4,
            size_threshold=8,
            max_wait_seconds=3600,
        )

    def assert_consistent_with_model(self, store, deltas):
        """
        Compare the implementation against a simple reference model.

        Snapshot(v) = delta[0] + ... + delta[v-1]
        """
        versions = store.list_versions("doc")

        # Every append must create exactly one version.
        self.assertEqual(len(versions), len(deltas))

        for expected_version, record in enumerate(versions, start=1):
            self.assertEqual(record["version"], expected_version)

            expected_snapshot = b"".join(deltas[:expected_version])

            if record["status"] == "Available":
                actual_snapshot = store.get_version(
                    "doc",
                    expected_version,
                )
                self.assertEqual(actual_snapshot, expected_snapshot)

            elif record["status"] == "PENDING":
                # A pending version must never be retrievable.
                with self.assertRaises(RuntimeError):
                    store.get_version("doc", expected_version)

            else:
                self.fail(f"Unexpected version status: {record['status']}")

        # History must remain valid after every operation.
        self.assertTrue(store.verify_history("doc"))

    @settings(
        max_examples=100,
        deadline=None,
    )
    @given(operations=operation_sequences)
    def test_random_append_seal_restart_recovery_sequences(
        self,
        operations,
    ):
        """
        Randomly execute append/seal/restart/recover operations.

        After each operation, check that every committed snapshot
        matches the reference model.
        """
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            deltas = []

            object_created = False

            for operation, payload in operations:

                if operation == "append":
                    result = store.append("doc", payload)
                    deltas.append(payload)
                    object_created = True

                    self.assertEqual(
                        result["version"],
                        len(deltas),
                    )

                elif operation == "seal":
                    if object_created:
                        store.seal("doc")

                elif operation == "restart":
                    store = self.make_store(tmp)

                    if object_created:
                        store.recover("doc")

                elif operation == "recover":
                    if object_created:
                        store.recover("doc")

                if object_created:
                    self.assert_consistent_with_model(store, deltas)

            if object_created:
                store.seal("doc")

                versions = store.list_versions("doc")

                self.assertEqual(len(versions), len(deltas))

                for version, record in enumerate(versions, start=1):
                    self.assertEqual(record["version"], version)
                    self.assertEqual(record["status"], "Available")

                    expected_snapshot = b"".join(deltas[:version])

                    self.assertEqual(
                        store.get_version("doc", version),
                        expected_snapshot,
                    )

                self.assertTrue(store.verify_history("doc"))
            else:
                self.assertEqual(deltas, [])

    @settings(
        max_examples=100,
        deadline=None,
    )
    @given(
        deltas=st.lists(
            st.binary(min_size=1, max_size=32),
            min_size=1,
            max_size=20,
        )
    )
    def test_all_versions_reconstruct_exact_append_history(self, deltas):
        """
        Append arbitrary deltas, seal once, and verify every snapshot.
        """
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)

            for delta in deltas:
                store.append("doc", delta)

            store.seal("doc")

            for version in range(1, len(deltas) + 1):
                expected = b"".join(deltas[:version])

                actual = store.get_version("doc", version)

                self.assertEqual(actual, expected)

            self.assertTrue(store.verify_history("doc"))


if __name__ == "__main__":
    unittest.main()
