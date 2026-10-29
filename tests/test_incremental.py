import tempfile
import unittest
import time
from unittest.mock import patch
from pathlib import Path

from engram.incremental import IncrementalStore


class IncrementalStoreTests(unittest.TestCase):
    def make_store(self, root, **kwargs):
        return IncrementalStore(root, data_shards=2, total_shards=4,
                                chunk_size=4, size_threshold=8,
                                max_wait_seconds=3600, **kwargs)

    def test_batching_and_version_retrieval(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            self.assertEqual(store.append("doc", b"abcd")["status"], "PENDING")
            self.assertEqual(store.append("doc", b"efgh")["status"], "Available")
            self.assertEqual(store.get_version("doc", 1), b"abcd")
            self.assertEqual(store.get_version("doc", 2), b"abcdefgh")
            self.assertTrue(store.verify_history("doc"))
            self.assertEqual([v["version"] for v in store.list_versions("doc")], [1, 2])

    def test_explicit_seal_and_restart_recovery_of_open_buffer(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            store.append("doc", b"one")
            # Reopening the store preserves an unsealed generation.
            reopened = self.make_store(tmp)
            self.assertEqual(reopened.list_versions("doc")[0]["status"], "PENDING")
            self.assertTrue(reopened.seal("doc"))
            self.assertEqual(reopened.get_version("doc", 1), b"one")
            self.assertTrue(reopened.verify_history("doc"))

    def test_maximum_wait_seal(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            store.append("doc", b"small")
            state = store._load("doc")
            state["open"]["started_at"] = 1.0
            store._save("doc", state)
            self.assertTrue(store.seal_expired_generations("doc", now=time.time() + 100.0))
            self.assertEqual(store.get_version("doc", 1), b"small")

    def test_duplicate_append_creates_distinct_monotonic_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            store.append("doc", b"same")
            store.append("doc", b"same")
            store.seal("doc")
            self.assertEqual(store.get_version("doc", 1), b"same")
            self.assertEqual(store.get_version("doc", 2), b"samesame")
            self.assertTrue(store.verify_history("doc"))


    def test_retrieval_uses_version_order_not_manifest_list_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            store.append("doc", b"AAAA")
            store.seal("doc")
            store.append("doc", b"BBBB")
            store.seal("doc")
            state = store._load("doc")
            state["generations"].reverse()
            store._save("doc", state)
            self.assertEqual(store.get_version("doc", 2), b"AAAABBBB")
            self.assertTrue(store.verify_history("doc"))


    def test_recovery_resumes_staged_seal(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            store.append("doc", b"tiny")
            state = store._load("doc")
            with patch.object(store, "_finish_seal", side_effect=RuntimeError("simulated crash")):
                with self.assertRaises(RuntimeError):
                    store._seal("doc", state)

            reopened = self.make_store(tmp)
            reopened.recover("doc")
            self.assertEqual(reopened.get_version("doc", 1), b"tiny")
            self.assertTrue(reopened.verify_history("doc"))

    def test_missing_generation_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            store.append("doc", b"AAAA")
            store.seal("doc")
            state = store._load("doc")
            state["generations"].clear()
            store._save("doc", state)
            self.assertFalse(store.verify_history("doc"))

    def test_pending_version_is_not_available_for_retrieval(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            store.append("doc", b"small")
            with self.assertRaises(RuntimeError):
                store.get_version("doc", 1)


if __name__ == "__main__":
    unittest.main()
