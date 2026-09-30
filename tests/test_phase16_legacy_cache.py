"""Phase 16 compatibility gates for legacy v4 cache entries."""
from __future__ import annotations

from pathlib import Path
import json
import tempfile
import unittest

from akuz_store import (
    cached_download,
    cached_report,
    load_store,
    report_summary,
    save_store,
    sha256,
)


class Phase16LegacyCacheTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="akuz-phase16-legacy-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "downloads").mkdir()
        (self.root / "reports").mkdir()

    def test_v43_style_entries_without_integrity_are_reused_safely(self):
        raw = self.root / "downloads" / "akuz_v4_legacy.log"
        raw.write_bytes(b"12:00:00.000,AKUZ,s,user: legacy\n")

        rid = "v4_20260930_120000_deadbeef"
        report = self.root / "reports" / rid
        (report / "data").mkdir(parents=True)
        (report / "index.html").write_text("<html>legacy</html>", encoding="utf-8")
        (report / "data" / "catalog.js").write_text(
            "window.AKUZ_DATA={};\n", encoding="utf-8")
        (report / "provenance.json").write_text(
            json.dumps({"sources": [], "kind": "single", "events": 1}),
            encoding="utf-8")

        # Shape matches the v4.3-era inventory: no integrity manifest,
        # no fingerprint-version metadata and an older snapshot dictionary.
        store = load_store(self.root)
        store["downloads"] = {
            "legacy-fid": {
                "path": str(raw),
                "sha256": sha256(raw),
                "size": raw.stat().st_size,
                "host": "legacy-host",
                "remote": "/srv/legacy.log",
                "mtime": 1,
                "snapshot": {"active": False},
            }
        }
        store["reports"] = {
            rid: {
                "id": rid,
                "key": "legacy-key",
                "label": "legacy.log · 2026-09-30",
                "kind": "single",
                "sources": [{
                    "name": "legacy.log",
                    "date": "2026-09-30",
                    "sha256": sha256(raw),
                    "remote_path": "/srv/legacy.log",
                    "host": "legacy-host",
                }],
                "events": 1,
                "lines": 1,
                "created": "2026-09-30T12:00:00",
            }
        }
        save_store(self.root, store)

        loaded = load_store(self.root)
        cached = cached_download(loaded, "legacy-fid")
        self.assertIsNotNone(cached)
        self.assertEqual(cached[0], raw)
        reused = cached_report(loaded, "legacy-key", self.root)
        self.assertIsNotNone(reused)
        self.assertEqual(reused["id"], rid)
        summary = report_summary(loaded, self.root)
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]["id"], rid)
        self.assertFalse(summary[0].get("invalidated"))

    def test_unknown_future_inventory_version_remains_fail_closed(self):
        cache = self.root / "cache"
        cache.mkdir()
        (cache / "inventory.json").write_text(
            json.dumps({"version": 999, "downloads": {}, "reports": {}}),
            encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Неизвестная версия"):
            load_store(self.root)


if __name__ == "__main__":
    unittest.main()
