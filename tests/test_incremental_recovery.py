"""Additional tests for incremental buffer cleanup and recovery.

Run from the project root:
    PYTHONPATH=src python -m unittest discover -s tests -v
"""
import tempfile
import unittest
from unittest.mock import patch

from engram.incremental import IncrementalStore


class IncrementalBufferRecoveryTests(unittest.TestCase):
    def make_store(self, root, **kwargs):
        return IncrementalStore(
            root,
            data_shards=2,
            total_shards=4,
            chunk_size=4,
            size_threshold=8,
            max_wait_seconds=3600,
            **kwargs,
        )

    def test_buffer_is_retained_when_shard_publication_fails_then_recovered(self):
        """A failed seal must preserve enough temporary data for retry."""
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            store.append("doc", b"tiny")

            buffer_path = store._buffer_path("doc")
            staging_path = store._staging_payload_path("doc")
            self.assertTrue(buffer_path.exists())
            self.assertEqual(buffer_path.read_bytes(), b"tiny")

            with patch.object(
                store.storage,
                "store_chunk",
                side_effect=RuntimeError("simulated shard publication failure"),
            ):
                with self.assertRaisesRegex(RuntimeError, "simulated shard publication failure"):
                    store.seal("doc")

            state = store._load("doc")
            self.assertIsNotNone(state["sealing"])
            self.assertTrue(buffer_path.exists(), "buffer must survive failed seal")
            self.assertEqual(buffer_path.read_bytes(), b"tiny")
            self.assertTrue(staging_path.exists(), "staged ciphertext must survive failed seal")

            # Retry with normal storage operations, simulating a process restart.
            reopened = self.make_store(tmp)
            reopened.recover("doc")
            self.assertEqual(reopened.get_version("doc", 1), b"tiny")
            self.assertTrue(reopened.verify_history("doc"))
            self.assertFalse(buffer_path.exists())
            self.assertFalse(staging_path.exists())

    def test_restart_after_commit_before_cleanup_is_idempotent(self):
        """If commit succeeds but cleanup fails, recovery only cleans temp files."""
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            store.append("doc", b"tiny")

            original_cleanup = store._cleanup_committed_temporary_files

            def crash_only_after_commit(object_id, state):
                # During the initial recover(), the open entries still exist.
                # Simulate a crash only when _finish_seal has committed the state.
                committed = (
                    state.get("sealing") is None
                    and not state["open"].get("entries")
                    and any(v["status"] == "Available" for v in state["versions"])
                )
                if committed:
                    raise RuntimeError("simulated crash before temporary-file cleanup")
                return original_cleanup(object_id, state)

            with patch.object(
                store,
                "_cleanup_committed_temporary_files",
                side_effect=crash_only_after_commit,
            ):
                with self.assertRaisesRegex(RuntimeError, "before temporary-file cleanup"):
                    store.seal("doc")

            state_after_crash = store._load("doc")
            self.assertIsNone(state_after_crash["sealing"])
            self.assertEqual(len(state_after_crash["generations"]), 1)
            self.assertEqual(state_after_crash["versions"][0]["status"], "Available")
            self.assertTrue(store._buffer_path("doc").exists())
            self.assertTrue(store._staging_payload_path("doc").exists())

            reopened = self.make_store(tmp)
            reopened.recover("doc")
            self.assertEqual(len(reopened._load("doc")["generations"]), 1)
            self.assertEqual(reopened.get_version("doc", 1), b"tiny")
            self.assertTrue(reopened.verify_history("doc"))
            self.assertFalse(reopened._buffer_path("doc").exists())
            self.assertFalse(reopened._staging_payload_path("doc").exists())

    def test_append_after_seal_and_restart_creates_next_generation(self):
        """A reopened store can append new data after an earlier generation seals."""
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            store.append("doc", b"AAAA")
            self.assertTrue(store.seal("doc"))

            reopened = self.make_store(tmp)
            reopened.append("doc", b"BBBB")
            self.assertTrue(reopened.seal("doc"))

            self.assertEqual(reopened.get_version("doc", 1), b"AAAA")
            self.assertEqual(reopened.get_version("doc", 2), b"AAAABBBB")
            self.assertEqual(len(reopened._load("doc")["generations"]), 2)
            self.assertTrue(reopened.verify_history("doc"))

    def test_uncommitted_buffer_tail_is_truncated_to_metadata_size(self):
        """Bytes written before a crash but not acknowledged by metadata are ignored."""
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            store.append("doc", b"abcd")
            state = store._load("doc")
            buffer_path = store._buffer_path("doc")
            self.assertEqual(state["open"]["buffer_size"], 4)

            # Simulate a crash after buffer replacement but before metadata save.
            buffer_path.write_bytes(b"abcdUNCOMMITTED")

            reopened = self.make_store(tmp)
            state = reopened._load("doc")
            self.assertEqual(reopened._read_buffer("doc", state), b"abcd")
            self.assertEqual(buffer_path.read_bytes(), b"abcd")

    def test_missing_nonempty_buffer_is_reported_as_corruption(self):
        """Metadata must not silently accept a missing buffer containing pending data."""
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            store.append("doc", b"tiny")
            buffer_path = store._buffer_path("doc")
            buffer_path.unlink()

            state = store._load("doc")
            with self.assertRaisesRegex(ValueError, "Open buffer is missing"):
                store._read_buffer("doc", state)


if __name__ == "__main__":
    unittest.main()
