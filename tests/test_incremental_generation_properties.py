import tempfile
import unittest

from hypothesis import given, settings, strategies as st

from engram.incremental import IncrementalStore


class IncrementalGenerationPropertyTests(unittest.TestCase):

    def make_store(self, root):
        return IncrementalStore(
            root,
            data_shards=2,
            total_shards=4,
            chunk_size=4,
            size_threshold=1024,
            max_wait_seconds=3600,
        )

    def create_generations(self, store):
        """
        Create three committed generations:
            G1 -> version 1
            G2 -> version 2
            G3 -> version 3
        """
        deltas = [
            b"first-delta",
            b"second-delta",
            b"third-delta",
        ]

        for delta in deltas:
            store.append("doc", delta)
            store.seal("doc")

        return deltas

    def assert_snapshots(self, store, deltas):
        for version in range(1, len(deltas) + 1):
            expected = b"".join(deltas[:version])
            actual = store.get_version("doc", version)
            self.assertEqual(actual, expected)

    # ---------------------------------------------------------
    # 1. DUPLICATE GENERATION / VERSION
    # ---------------------------------------------------------

    def test_duplicate_version_record_is_rejected(self):
        """
        A duplicated version record must invalidate history.
        """
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            self.create_generations(store)

            state = store._load("doc")

            # Duplicate version 1 in version metadata.
            duplicate = dict(state["versions"][0])
            state["versions"].append(duplicate)

            store._save("doc", state)

            self.assertFalse(store.verify_history("doc"))

            with self.assertRaisesRegex(
                ValueError,
                "Duplicate version record",
            ):
                store.get_version("doc", 1)

    def test_duplicate_generation_is_rejected(self):
        """
        A duplicated generation must not be accepted as valid history.
        """
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            self.create_generations(store)

            state = store._load("doc")

            duplicate = dict(state["generations"][0])
            state["generations"].append(duplicate)

            store._save("doc", state)

            self.assertFalse(store.verify_history("doc"))

            with self.assertRaises(ValueError):
                store.get_version("doc", 1)

    def test_duplicate_version_inside_generation_is_rejected(self):
        """
        A version must not appear twice inside one generation.
        """
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            self.create_generations(store)

            state = store._load("doc")

            duplicate = dict(state["generations"][0]["entries"][0])
            state["generations"][0]["entries"].append(duplicate)

            store._save("doc", state)

            self.assertFalse(store.verify_history("doc"))

            with self.assertRaises(ValueError):
                store.get_version("doc", 1)

    # ---------------------------------------------------------
    # 2. MISSING GENERATION
    # ---------------------------------------------------------

    def test_missing_middle_generation_is_detected(self):
        """
        Removing G2 must create a version gap.
        G1 covers version 1; G3 covers version 3.
        """
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            deltas = self.create_generations(store)

            state = store._load("doc")

            self.assertEqual(len(state["generations"]), 3)

            # Remove generation containing version 2.
            state["generations"] = [
                generation
                for generation in state["generations"]
                if int(generation["start_version"]) != 2
            ]

            store._save("doc", state)

            self.assertFalse(store.verify_history("doc"))

            # Version 3 cannot be reconstructed across the missing G2.
            with self.assertRaises(ValueError):
                store.get_version("doc", 3)

    def test_missing_generation_at_history_tail_is_detected(self):
        """
        Removing the last generation must not leave an apparently
        valid history while version records still claim it is Available.
        """
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            self.create_generations(store)

            state = store._load("doc")

            state["generations"] = state["generations"][:-1]

            store._save("doc", state)

            self.assertFalse(store.verify_history("doc"))

            with self.assertRaises(ValueError):
                store.get_version("doc", 3)

    # ---------------------------------------------------------
    # 3. OUT-OF-ORDER GENERATIONS / VERSION RECORDS
    # ---------------------------------------------------------

    @settings(max_examples=20, deadline=None)
    @given(order=st.permutations((0, 1, 2)))
    def test_arbitrary_generation_and_version_metadata_order(
        self,
        order,
    ):
        """
        Generation and version metadata may be stored in arbitrary
        list order. Retrieval must still follow logical version order.
        """
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            deltas = self.create_generations(store)

            state = store._load("doc")

            original_generations = list(state["generations"])
            original_versions = list(state["versions"])

            state["generations"] = [original_generations[index] for index in order]

            state["versions"] = [original_versions[index] for index in order]

            store._save("doc", state)

            # The implementation should normalize generation order.
            self.assertTrue(store.verify_history("doc"))

            # All snapshots must remain exactly correct.
            self.assert_snapshots(store, deltas)

    def test_out_of_order_entries_inside_generation_are_rejected(self):
        """
        Unlike generation-list order, entries inside one generation
        must remain contiguous and ordered.
        """
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)

            # Two versions in the same generation.
            store.append("doc", b"first")
            store.append("doc", b"second")
            store.seal("doc")

            state = store._load("doc")

            self.assertEqual(len(state["generations"]), 1)
            self.assertEqual(
                len(state["generations"][0]["entries"]),
                2,
            )

            entries = state["generations"][0]["entries"]
            entries.reverse()

            store._save("doc", state)

            self.assertFalse(store.verify_history("doc"))

            with self.assertRaises(ValueError):
                store.get_version("doc", 1)


if __name__ == "__main__":
    unittest.main()
