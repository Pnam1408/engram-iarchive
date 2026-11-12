"""Integration tests for per-object MMR history commitments."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from engram.incremental import IncrementalStore
from engram.mmr import (
    verify_consistency_proof,
    verify_inclusion_proof,
)
from engram.mmr.accumulator import MMRAccumulator


class IncrementalMMRTests(unittest.TestCase):
    def make_store(self, root: str) -> IncrementalStore:
        return IncrementalStore(
            root,
            data_shards=2,
            total_shards=4,
            chunk_size=4,
            size_threshold=8,
            max_wait_seconds=3600,
        )

    def test_new_object_starts_with_empty_mmr(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)

            state = store._create_initial_state("doc")

            self.assertEqual(state["schema_version"], store.STATE_VERSION)
            self.assertIn("mmr", state)
            self.assertEqual(state["mmr"]["leaf_count"], 0)
            self.assertEqual(state["mmr"]["last_version"], 0)
            self.assertEqual(
                MMRAccumulator.from_dict(state["mmr"]).root.hex(),
                state["mmr"]["root"],
            )

    def test_pending_versions_are_not_mmr_leaves(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            store.append("doc", b"abc")
            self.assertEqual(store._load("doc")["mmr"]["leaf_count"], 0)
            store.seal("doc")
            self.assertEqual(store._load("doc")["mmr"]["leaf_count"], 1)

    def test_batched_versions_each_get_one_leaf_and_proofs_verify(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            store.append("doc", b"abcd")
            store.append("doc", b"efgh")
            state = store._load("doc")
            self.assertEqual(state["mmr"]["leaf_count"], 2)
            root = store.get_mmr_root("doc")
            for version in (1, 2):
                proof = store.get_inclusion_proof("doc", version)
                self.assertTrue(verify_inclusion_proof(proof, root))

    def test_multiple_generations_extend_one_object_mmr(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            store.append("doc", b"AAAA")
            store.seal("doc")
            old_root = store.get_mmr_root("doc")
            store.append("doc", b"BBBB")
            store.seal("doc")
            proof = store.get_consistency_proof("doc", 1)
            self.assertTrue(
                verify_consistency_proof(proof, old_root, store.get_mmr_root("doc"))
            )
            self.assertEqual(store._load("doc")["mmr"]["leaf_count"], 2)
            self.assertTrue(store.verify_history("doc"))

    def test_mmr_tampering_is_detected_by_history_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            store.append("doc", b"AAAA")
            store.seal("doc")
            state = store._load("doc")
            state["mmr"]["root"] = "00" * 32
            store._save("doc", state)
            self.assertFalse(store.verify_history("doc"))


if __name__ == "__main__":
    unittest.main()
