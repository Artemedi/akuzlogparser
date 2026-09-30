"""Phase 16 cache-clear correctness for mixed/current/old cache roots."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from akuz_store import clear_cache, load_store, save_store


class Phase16CacheClearTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="akuz-phase16-cache-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.current = self.root / "downloads-current"
        self.old = self.root / "downloads-old"
        self.current.mkdir()
        self.old.mkdir()

    def _entry(self, path: Path):
        return {
            "path": str(path),
            "sha256": "0" * 64,
            "size": path.stat().st_size if path.exists() else 0,
            "host": "synthetic",
            "remote": "/synthetic/" + path.name,
            "mtime": 1,
            "snapshot": {},
        }

    def test_clear_preserves_inventory_for_old_unconfigured_cache_root(self):
        current_file = self.current / "akuz_v4_current.log"
        old_file = self.old / "akuz_v4_old.log"
        current_file.write_bytes(b"current")
        old_file.write_bytes(b"old")
        store = load_store(self.root)
        store["downloads"] = {
            "current": self._entry(current_file),
            "old": self._entry(old_file),
        }
        save_store(self.root, store)

        result = clear_cache(
            self.root, SimpleNamespace(local_dest=self.current),
            load_store(self.root), False)

        self.assertEqual(result["downloads_removed"], 1)
        self.assertEqual(result["downloads_retained"], 1)
        self.assertFalse(current_file.exists())
        self.assertTrue(old_file.exists())
        after = load_store(self.root)
        self.assertNotIn("current", after["downloads"])
        self.assertIn("old", after["downloads"])

    def test_missing_entry_inside_allowed_root_is_forgotten_without_delete_count(self):
        missing = self.current / "akuz_v4_missing.log"
        store = load_store(self.root)
        store["downloads"] = {
            "missing": {
                "path": str(missing),
                "sha256": "0" * 64,
                "size": 123,
            }
        }
        save_store(self.root, store)

        result = clear_cache(
            self.root, SimpleNamespace(local_dest=self.current),
            load_store(self.root), False)

        self.assertEqual(result["downloads_removed"], 0)
        self.assertEqual(result["downloads_retained"], 0)
        self.assertEqual(load_store(self.root)["downloads"], {})

    def test_external_or_wrongly_named_entry_is_not_unindexed_or_deleted(self):
        external = self.root / "akuz_v4_external.log"
        wrong_name = self.current / "operator.log"
        external.write_bytes(b"external")
        wrong_name.write_bytes(b"operator")
        store = load_store(self.root)
        store["downloads"] = {
            "external": self._entry(external),
            "wrong-name": self._entry(wrong_name),
        }
        save_store(self.root, store)

        result = clear_cache(
            self.root, SimpleNamespace(local_dest=self.current),
            load_store(self.root), False)

        self.assertEqual(result["downloads_removed"], 0)
        self.assertEqual(result["downloads_retained"], 2)
        self.assertTrue(external.exists())
        self.assertTrue(wrong_name.exists())
        self.assertEqual(
            set(load_store(self.root)["downloads"]),
            {"external", "wrong-name"},
        )

    def test_multiple_configured_roots_are_all_cleared(self):
        a = self.current / "akuz_v4_a.log"
        b = self.old / "akuz_v4_b.log"
        a.write_bytes(b"a")
        b.write_bytes(b"b")
        store = load_store(self.root)
        store["downloads"] = {"a": self._entry(a), "b": self._entry(b)}
        save_store(self.root, store)

        result = clear_cache(
            self.root,
            [SimpleNamespace(local_dest=self.current),
             SimpleNamespace(local_dest=self.old)],
            load_store(self.root), False)

        self.assertEqual(result["downloads_removed"], 2)
        self.assertEqual(result["downloads_retained"], 0)
        self.assertFalse(a.exists())
        self.assertFalse(b.exists())
        self.assertEqual(load_store(self.root)["downloads"], {})


if __name__ == "__main__":
    unittest.main()
