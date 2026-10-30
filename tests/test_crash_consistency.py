#tests/test_crash_consistency.py
import tempfile
import unittest
from unittest.mock import patch

from engram.incremental import IncrementalStore


class CrashConsistencyTests(unittest.TestCase):

    def make_store(self, root):
        return IncrementalStore(
            root,
            data_shards=2,
            total_shards=4,
            chunk_size=4,
            size_threshold=8,
            max_wait_seconds=3600,
        )

    def prepare_pending_version(self, store):
        """Create one version that has not been sealed yet."""
        result = store.append("doc", b"tiny")
        self.assertEqual(result["status"], "PENDING")

    def test_crash_after_staging_before_sealing_state_save(self):
        """
        Crash after staging ciphertext is written but before
        the sealing metadata is saved.
        """
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            self.prepare_pending_version(store)

            original_save = store._save

            def simulated_crash(object_id, state):
                if state.get("sealing") is not None:
                    raise RuntimeError("simulated crash before sealing state save")
                return original_save(object_id, state)

            with patch.object(store, "_save", side_effect=simulated_crash):
                with self.assertRaises(RuntimeError):
                    state = store._load("doc")
                    store._seal("doc", state)

            # The durable state should still contain an open pending version.
            disk_state = store._load("doc")
            self.assertIsNone(disk_state["sealing"])
            self.assertEqual(disk_state["open"]["entries"][0]["version"], 1)
            self.assertEqual(disk_state["versions"][0]["status"], "PENDING")

            # Restart and retry sealing.
            reopened = self.make_store(tmp)
            reopened.seal("doc")

            self.assertEqual(reopened.get_version("doc", 1), b"tiny")
            self.assertTrue(reopened.verify_history("doc"))

    def test_crash_after_shard_publication_before_generation_commit(self):
        """
        Crash after shards have been published and read back,
        but before generation metadata is committed.
        """
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            self.prepare_pending_version(store)

            # _validate_staged_generation is called after shard publication
            # and read-back verification, immediately before metadata commit.
            with patch.object(
                store,
                "_validate_staged_generation",
                side_effect=RuntimeError("simulated crash before generation commit"),
            ):
                with self.assertRaises(RuntimeError):
                    state = store._load("doc")
                    store._seal("doc", state)

            disk_state = store._load("doc")

            # Shards have been published, but object metadata is not committed.
            self.assertIsNotNone(disk_state["sealing"])
            self.assertEqual(disk_state["generations"], [])
            self.assertEqual(disk_state["versions"][0]["status"], "PENDING")

            generation_id = disk_state["sealing"]["generation_id"]

            # The storage manifest should already exist.
            store.storage.get_chunk_metadata(generation_id)

            # Recovery must reuse the published shards and finish the commit.
            reopened = self.make_store(tmp)
            reopened.recover("doc")

            recovered_state = reopened._load("doc")

            self.assertIsNone(recovered_state["sealing"])
            self.assertEqual(len(recovered_state["generations"]), 1)
            self.assertEqual(recovered_state["versions"][0]["status"], "Available")
            self.assertEqual(
                reopened.get_version("doc", 1),
                b"tiny",
            )
            self.assertTrue(reopened.verify_history("doc"))

    def test_crash_after_generation_commit_before_cleanup(self):
        """
        Crash after object metadata is committed but before
        temporary staging files are cleaned up.
        """
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            self.prepare_pending_version(store)

            with patch.object(
                store,
                "_cleanup_committed_temporary_files",
                side_effect=RuntimeError("simulated crash before cleanup"),
            ):
                with self.assertRaises(RuntimeError):
                    state = store._load("doc")
                    store._seal("doc", state)

            # The commit must already be durable.
            disk_state = store._load("doc")

            self.assertIsNone(disk_state["sealing"])
            self.assertEqual(len(disk_state["generations"]), 1)
            self.assertEqual(disk_state["versions"][0]["status"], "Available")

            # Reopening should clean up temporary files without losing data.
            reopened = self.make_store(tmp)
            reopened.recover("doc")

            recovered_state = reopened._load("doc")

            self.assertEqual(len(recovered_state["generations"]), 1)
            self.assertEqual(recovered_state["versions"][0]["status"], "Available")
            self.assertEqual(reopened.get_version("doc", 1), b"tiny")
            self.assertTrue(reopened.verify_history("doc"))

    def test_recovery_is_idempotent(self):
        """
        Repeated recovery must not create duplicate generations
        or alter already committed versions.
        """
        with tempfile.TemporaryDirectory() as tmp:
            store = self.make_store(tmp)
            self.prepare_pending_version(store)

            # Leave the object in the staged-sealing state.
            with patch.object(
                store,
                "_finish_seal",
                side_effect=RuntimeError("simulated crash during sealing"),
            ):
                with self.assertRaises(RuntimeError):
                    state = store._load("doc")
                    store._seal("doc", state)

            reopened = self.make_store(tmp)

            # First recovery completes the generation.
            reopened.recover("doc")
            state_after_first_recovery = reopened._load("doc")

            self.assertEqual(len(state_after_first_recovery["generations"]), 1)

            # Further recoveries must be harmless.
            reopened.recover("doc")
            reopened.recover("doc")
            reopened.recover("doc")

            final_state = reopened._load("doc")

            self.assertEqual(len(final_state["generations"]), 1)
            self.assertEqual(len(final_state["versions"]), 1)
            self.assertEqual(final_state["versions"][0]["status"], "Available")
            self.assertEqual(reopened.get_version("doc", 1), b"tiny")
            self.assertTrue(reopened.verify_history("doc"))


if __name__ == "__main__":
    unittest.main()
