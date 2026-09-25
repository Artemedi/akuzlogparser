"""Existing report UI upgrades must never rewrite event catalogs or raw shards."""
from pathlib import Path
import tempfile
import unittest

from scripts.refresh_report_ui import ASSETS, SOURCE, refresh


class RefreshReportUITests(unittest.TestCase):
    def test_dry_run_and_apply_only_touch_ui(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "reports" / "v4_test"
            data = report / "data"
            data.mkdir(parents=True)
            (data / "catalog.js").write_bytes(b"window.AKUZ_DATA=SENSITIVE")
            (data / "raw_00000.js").write_bytes(b"window.AKUZ_RAW=SENSITIVE")
            for name in ASSETS:
                (report / name).write_text("old ui", encoding="utf-8")
            other = root / "reports" / "other"
            other.mkdir()
            (other / "index.html").write_text("other", encoding="utf-8")
            self.assertEqual(refresh(root, apply=False), 1)
            self.assertEqual((report / "index.js").read_text(encoding="utf-8"),
                             "old ui")
            self.assertEqual(refresh(root, apply=True), 1)
            for name in ASSETS:
                self.assertEqual((report / name).read_bytes(),
                                 (SOURCE / name).read_bytes())
            self.assertEqual((data / "catalog.js").read_bytes(),
                             b"window.AKUZ_DATA=SENSITIVE")
            self.assertEqual((data / "raw_00000.js").read_bytes(),
                             b"window.AKUZ_RAW=SENSITIVE")
            self.assertEqual((other / "index.html").read_text(encoding="utf-8"),
                             "other")


if __name__ == "__main__":
    unittest.main()
