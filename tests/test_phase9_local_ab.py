"""Guards for alternating Phase 9 Python no-spool/spool control."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import bench_phase9_local_ab as ab
from scripts.bench_phase9_baseline import create_sources


class LocalABTests(unittest.TestCase):
    def test_order_rejects_unbalanced_or_non_alternating_series(self):
        ab.validate_order(ab.ORDER)
        for wrong in (("control", "spool") * 3,
                      ("control",) * 6, ab.ORDER[:-1]):
            with self.assertRaises(ValueError):
                ab.validate_order(wrong)

    def test_signature_comparison_rejects_missing_and_changed_fields(self):
        sig = {"reports": {"a": "abc"}, "exports": {"x": "def"},
               "single_events": 13}
        self.assertTrue(all(ab.compare_signatures(sig, dict(sig)).values()))
        for broken in ({"reports": {"a": "other"}, "exports": {"x": "def"},
                        "single_events": 13},
                       {"reports": {"a": "abc"}, "single_events": 13},
                       dict(sig, extra="unexpected")):
            with self.assertRaises(AssertionError):
                ab.compare_signatures(sig, broken)

    def test_alternating_calls_and_metrics(self):
        sig = {"inventory": "stable", "single_events": 13}
        def worker(root, source, *, use_derived_spool):
            return sig, {"wall_s": 3.0 if use_derived_spool else 4.0,
                         "cpu_s": 2.0 if use_derived_spool else 3.0,
                         "memory": {"scope": "isolated_process_tree_lifetime"}}
        with patch.object(ab, "python_build_isolated", side_effect=worker) as mocked, \
             patch.object(ab, "cleanup_trial") as cleaned:
            result = ab.trial_series(Path("owned"), Path("synthetic"))
        self.assertEqual(mocked.call_count, 6)
        self.assertEqual(cleaned.call_count, 6)
        self.assertEqual([call.kwargs["use_derived_spool"]
                          for call in mocked.call_args_list],
                         [False, True, True, False, False, True])
        self.assertEqual(result["summary"]["control"]["wall_median_s"], 4.0)
        self.assertEqual(result["summary"]["spool"]["cpu_median_s"], 2.0)
        self.assertTrue(all(all(row["checks"].values())
                            for row in result["trials"]))

    def test_cleanup_only_owned_trial(self):
        ab.DIAG.mkdir(exist_ok=True)
        home = Path(tempfile.mkdtemp(prefix="phase9_frozen_", dir=ab.DIAG))
        (home / ab.MARKER).write_text("disposable\n", encoding="ascii")
        good = home / "trial_1_control"
        other = home / "trial_2_spool"
        good.mkdir()
        other.mkdir()
        try:
            with self.assertRaises(RuntimeError):
                ab.cleanup_trial(other, home, 1, "control")
            self.assertTrue(other.exists())
            ab.cleanup_trial(good, home, 1, "control")
            self.assertFalse(good.exists())
            self.assertTrue(other.exists())
        finally:
            ab.cleanup_owned_workspace(home)

    @unittest.skipUnless(sys.platform == "win32", "Windows isolated workers")
    def test_two_modes_real_synthetic_builds(self):
        ab.DIAG.mkdir(exist_ok=True)
        home = Path(tempfile.mkdtemp(prefix="phase9_frozen_", dir=ab.DIAG))
        (home / ab.MARKER).write_text("disposable\n", encoding="ascii")
        try:
            source = home / "synthetic_sources"
            create_sources(source, 4, 48)
            left, first = ab.python_build_isolated(
                home / "synthetic_no_spool", source, use_derived_spool=False)
            right, second = ab.python_build_isolated(
                home / "synthetic_spool", source, use_derived_spool=True)
            self.assertTrue(all(ab.compare_signatures(left, right).values()))
            self.assertEqual(left["single_events"], 13)
            self.assertGreater(first["memory"]["samples"], 0)
            self.assertGreater(second["memory"]["samples"], 0)
        finally:
            ab.cleanup_owned_workspace(home)
        self.assertFalse(home.exists())

    @unittest.skipUnless(sys.platform == "win32", "Windows isolated workers")
    def test_full_synthetic_six_trial_series_and_cleanup(self):
        ab.DIAG.mkdir(exist_ok=True)
        home = Path(tempfile.mkdtemp(prefix="phase9_frozen_", dir=ab.DIAG))
        (home / ab.MARKER).write_text("disposable\n", encoding="ascii")
        try:
            source = home / "synthetic_sources"
            create_sources(source, 4, 48)
            result = ab.trial_series(home, source)
            self.assertEqual(result["single_events"], 13)
            self.assertEqual(len(result["trials"]), 6)
            self.assertEqual(len(list(home.glob("trial_*"))), 0)
            self.assertEqual(len(list(source.glob("*.log"))), 3)
            self.assertTrue(all(row["metrics"]["disk_bytes"]["reports"] > 0
                                for row in result["trials"]))
        finally:
            ab.cleanup_owned_workspace(home)
        self.assertFalse(home.exists())


if __name__ == "__main__":
    unittest.main()
