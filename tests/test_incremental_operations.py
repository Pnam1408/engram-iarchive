from __future__ import annotations

import tempfile
import unittest

from engram.incremental import IncrementalStore
from engram.mmr import verify_inclusion_proof


class IncrementalOperationTests(unittest.TestCase):
    def make_store(self, root: str) -> IncrementalStore:
        return IncrementalStore(
            root,
            data_shards=2,
            total_shards=4,
            chunk_size=4,
            size_threshold=1024,
            max_wait_seconds=3600,
        )

    def test_append_insert_delete_preserve_immutable_versions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            store.append("doc", b"abcdefgh")
            store.seal("doc")
            self.assertEqual(store.get_version("doc", 1), b"abcdefgh")

            inserted = store.insert("doc", 3, b"XYZ")
            self.assertEqual(inserted["operation"], "insert")
            store.seal("doc")
            self.assertEqual(store.get_version("doc", 1), b"abcdefgh")
            self.assertEqual(store.get_version("doc", 2), b"abcXYZdefgh")
            insert_manifest = store._load("doc")["mmr"]["manifests"][1]
            self.assertEqual(insert_manifest["operation"], "insert")
            self.assertEqual(insert_manifest["offset"], 3)
            insert_proof = store.get_inclusion_proof("doc", 2)
            self.assertTrue(verify_inclusion_proof(insert_proof, store.get_mmr_root("doc")))

            deleted = store.delete("doc", 2, 5)
            self.assertEqual(deleted["operation"], "delete")
            self.assertEqual(deleted["delta_size_bytes"], 0)
            store.seal("doc")
            self.assertEqual(store.get_version("doc", 3), b"abefgh")
            self.assertEqual(store.get_version("doc", 2), b"abcXYZdefgh")
            self.assertTrue(store.verify_history("doc"))

    def test_insert_at_boundaries_and_append_after_delete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            store.append("doc", b"middle")
            store.seal("doc")
            store.insert("doc", 0, b"<")
            store.seal("doc")
            store.insert("doc", 7, b">")
            store.seal("doc")
            store.delete("doc", 0, 1)
            store.seal("doc")
            store.append("doc", b"!")
            store.seal("doc")

            self.assertEqual(store.get_version("doc", 2), b"<middle")
            self.assertEqual(store.get_version("doc", 3), b"<middle>")
            self.assertEqual(store.get_version("doc", 4), b"middle>")
            self.assertEqual(store.get_version("doc", 5), b"middle>!")
            self.assertTrue(store.verify_history("doc"))

    def test_invalid_insert_and_delete_do_not_allocate_versions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            store.append("doc", b"abc")
            store.seal("doc")
            with self.assertRaises(ValueError):
                store.insert("doc", 4, b"x")
            with self.assertRaises(ValueError):
                store.delete("doc", 2, 2)
            self.assertEqual([v["version"] for v in store.list_versions("doc")], [1])
            self.assertTrue(store.verify_history("doc"))

    def test_operations_can_be_batched_in_one_open_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            store.append("doc", b"abc")
            store.insert("doc", 1, b"X")
            store.delete("doc", 0, 1)
            store.seal("doc")
            self.assertEqual(store.get_version("doc", 1), b"abc")
            self.assertEqual(store.get_version("doc", 2), b"aXbc")
            self.assertEqual(store.get_version("doc", 3), b"Xbc")
            self.assertTrue(store.verify_history("doc"))

    def test_schema_v3_append_history_migrates_without_changing_mmr(self) -> None:
        import json

        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            store.append("doc", b"legacy")
            store.seal("doc")
            state = store._load("doc")
            original_root = store.get_mmr_root("doc")
            state["schema_version"] = 3
            fields = ("operation", "offset", "delete_length", "storage_size_bytes")
            for record in state["versions"]:
                for field in fields:
                    record.pop(field, None)
            for generation in state["generations"]:
                for record in generation["entries"]:
                    for field in fields:
                        record.pop(field, None)
            store._state_path("doc").write_text(json.dumps(state), encoding="utf-8")

            reopened = self.make_store(tmp)
            self.assertEqual(reopened.get_version("doc", 1), b"legacy")
            self.assertEqual(reopened.get_mmr_root("doc"), original_root)
            self.assertTrue(reopened.verify_history("doc"))

    def test_pending_delete_recovers_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            store.append("doc", b"abcdef")
            store.seal("doc")
            store.delete("doc", 1, 3)

            reopened = self.make_store(tmp)
            self.assertEqual(reopened.list_versions("doc")[-1]["status"], "PENDING")
            reopened.seal("doc")
            self.assertEqual(reopened.get_version("doc", 2), b"aef")
            self.assertTrue(reopened.verify_history("doc"))


if __name__ == "__main__":
    unittest.main()
