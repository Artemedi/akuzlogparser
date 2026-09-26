"""Phase 9.0 runtime A/B orchestration guards."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import bench_phase9_python_frozen_ab as bench


def signature():
    return {
        "inventory": "i", "reports": {"a": "r"}, "sql": {"x": "s"},
        "exports": {"y": "e"}, "single_events": 13,
    }


def metrics(wall, cpu, ws, private):
    return {
        "wall_s": wall, "cpu_s": cpu,
        "memory": {"working_set_bytes": ws, "private_bytes": private},
    }


class RuntimeABTests(unittest.TestCase):
    def test_order_is_fixed_ab_ba_ab(self):
        bench.validate_order(bench.ORDER)
        with self.assertRaises(ValueError):
            bench.validate_order(("python", "frozen") * 3)

    def test_signature_mismatch_fails_closed(self):
        reference = signature()
        self.assertTrue(all(bench.compare_signatures(reference, dict(reference)).values()))
        changed = dict(reference, single_events=12)
        with self.assertRaises(AssertionError):
            bench.compare_signatures(reference, changed)
    def test_trial_series_runs_three_per_runtime_and_cleans_each(self):
        calls = []
        cleaned = []
        def python_build(root, sources):
            calls.append(("python", root.name))
            return signature(), metrics(4.0, 3.0, 100, 200)
        def frozen_build(root, sources, archive):
            calls.append(("frozen", root.name))
            return signature(), metrics(3.0, 2.0, 110, 210)
        def cleanup(root, home, index, runtime):
            cleaned.append((root.name, index, runtime))
        with TemporaryDirectory() as td, \
             patch.object(bench, "python_build_isolated", side_effect=python_build), \
             patch.object(bench, "frozen_build", side_effect=frozen_build), \
             patch.object(bench, "cleanup_trial", side_effect=cleanup):
            result = bench.trial_series(
                Path(td), Path(td) / "sources", Path(td) / "archive.zip")
        self.assertEqual([x[0] for x in calls], list(bench.ORDER))
        self.assertEqual(len(cleaned), 6)
        self.assertEqual(result["single_events"], 13)
        self.assertEqual(result["summary"]["python"]["wall_median_s"], 4.0)
        self.assertEqual(result["summary"]["frozen"]["cpu_median_s"], 2.0)
        self.assertEqual(result["summary"]["python"]["working_set_bytes"],
                         [100, 100, 100])


if __name__ == "__main__":
    unittest.main()
