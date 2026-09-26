"""Fault-injection guards for local inventory and report publication."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import akuz_app
from akuz_store import load_store, save_store


class InventoryAtomicityTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "20260925_synthetic.log"
        self.source.write_text("12:00:00.000,AKUZ,r,u: synthetic\n",
                               encoding="utf-8")
        self.store = load_store(self.root)
        self.store["downloads"]["existing"] = {"marker": "keep"}
        save_store(self.root, self.store)

    @staticmethod
    def generator(raw, output, base, chunk, top):
        output.mkdir(parents=True)
        (output / "index.html").write_text("temporary", encoding="utf-8")
        (output / "data").mkdir()
        (output / "data" / "catalog.js").write_text("window.AKUZ_DATA={}", encoding="utf-8")
        return {"events": 1, "physical_lines": 1}

    def test_failed_replace_preserves_old_inventory_and_removes_temp(self):
        inventory = self.root / "cache" / "inventory.json"
        old_bytes = inventory.read_bytes()
        self.store["downloads"]["new"] = {"marker": "new"}
        with patch.object(Path, "replace", side_effect=OSError("disk failure")):
            with self.assertRaisesRegex(OSError, "disk failure"):
                save_store(self.root, self.store)
        self.assertEqual(inventory.read_bytes(), old_bytes)
        self.assertFalse((inventory.parent / "inventory.json.tmp").exists())
        self.assertEqual(load_store(self.root)["downloads"],
                         {"existing": {"marker": "keep"}})

    def test_partial_inventory_write_is_removed_without_losing_previous(self):
        inventory = self.root / "cache" / "inventory.json"
        previous = inventory.read_bytes()
        original_write = Path.write_text

        def partial_then_fail(path, *args, **kwargs):
            if path.name == "inventory.json.tmp":
                original_write(path, "{incomplete", encoding="utf-8")
                raise OSError("synthetic partial write")
            return original_write(path, *args, **kwargs)

        with patch.object(Path, "write_text", partial_then_fail):
            with self.assertRaisesRegex(OSError, "synthetic partial write"):
                save_store(self.root, self.store)
        self.assertEqual(inventory.read_bytes(), previous)
        self.assertFalse((inventory.parent / "inventory.json.tmp").exists())
        self.assertEqual(load_store(self.root)["downloads"],
                         {"existing": {"marker": "keep"}})

    def test_report_replace_failure_then_retry_has_no_orphan_or_duplicate(self):
        inventory = self.root / "cache" / "inventory.json"
        old_bytes = inventory.read_bytes()
        with patch.object(Path, "replace", side_effect=OSError("disk failure")):
            with self.assertRaisesRegex(OSError, "disk failure"):
                akuz_app._publish(self.root, self.store, "synthetic-key",
                    self.source, None, [], "synthetic", "single",
                    gen_fn=self.generator)
        self.assertEqual(inventory.read_bytes(), old_bytes)
        self.assertFalse((inventory.parent / "inventory.json.tmp").exists())
        self.assertFalse(list((self.root / "reports").glob("v4_*")))
        self.assertEqual(self.store["reports"], {})
        report = akuz_app._publish(self.root, self.store, "synthetic-key",
            self.source, None, [], "synthetic", "single",
            gen_fn=self.generator)
        self.assertFalse(report["reused"])
        self.assertEqual(len(load_store(self.root)["reports"]), 1)
        self.assertEqual(len(list((self.root / "reports").glob("v4_*"))), 1)


if __name__ == "__main__":
    unittest.main()
