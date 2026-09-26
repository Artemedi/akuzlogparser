"""Phase 9.0: same child-process sampling scope for Python and frozen builds."""
from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import bench_phase9_frozen_real as bench
from scripts.bench_phase9_baseline import create_sources
from scripts.phase9_memory import sample


class _TwoTicks:
    def __init__(self):
        self.ticks = 0

    def wait(self, _interval):
        self.ticks += 1
        return self.ticks > 1


class FrozenMemoryTests(unittest.TestCase):
    def test_process_tree_sampler_records_simultaneous_and_os_peaks(self):
        stats = {"samples": 0, "working_set_bytes": 0, "private_bytes": 0,
                 "scope": "isolated_process_tree_lifetime",
                 "os_peak_ws_per_pid": {},
                 "os_peak_pagefile_per_pid": {},
                 "unreadable_samples": 0, "cpu_unreadable_samples": 0,
                 "sampled_tree_lifetime_cpu_s": 0.0,
                 "cpu_time_s_per_pid": {}}
        samples = {
            11: {"working_set_bytes": 100, "private_bytes": 150,
                 "peak_working_set_bytes": 120, "peak_pagefile_bytes": 180,
                 "cpu_time_s": 1.25},
            22: {"working_set_bytes": 40, "private_bytes": 50,
                 "peak_working_set_bytes": 60, "peak_pagefile_bytes": 70,
                 "cpu_time_s": .75},
        }
        with patch.object(bench, "tree_pids", return_value={22, 11}), \
             patch.object(bench, "sample", side_effect=lambda pid: samples[pid]):
            bench.monitor_pid(11, _TwoTicks(), stats)
        self.assertEqual(stats["samples"], 1)
        self.assertEqual(stats["working_set_bytes"], 140)
        self.assertEqual(stats["private_bytes"], 200)
        self.assertEqual(stats["os_peak_ws_per_pid"], {11: 120, 22: 60})
        self.assertEqual(stats["os_peak_pagefile_per_pid"], {11: 180, 22: 70})
        self.assertEqual(stats["sampled_tree_lifetime_cpu_s"], 2.0)
        self.assertEqual(stats["cpu_time_s_per_pid"], {11: 1.25, 22: .75})

    @unittest.skipUnless(sys.platform == "win32", "Windows child-process memory")
    def test_isolated_python_worker_excludes_controller_pid(self):
        bench.DIAG.mkdir(exist_ok=True)
        home = Path(tempfile.mkdtemp(prefix="phase9_frozen_",
                                     dir=bench.DIAG))
        (home / bench.MARKER).write_text("disposable\n", encoding="ascii")
        try:
            source = home / "synthetic_sources"
            create_sources(source, 4, 48)
            signatures, metrics = bench.python_build_isolated(
                home / "python", source)
            self.assertEqual(signatures["single_events"], 13)
            self.assertGreater(metrics["wall_s"], 0)
            self.assertGreater(metrics["memory"]["samples"], 0)
            self.assertEqual(metrics["memory"]["scope"],
                             "isolated_process_tree_lifetime")
            self.assertTrue(metrics["memory"]["os_peak_ws_per_pid"])
            self.assertGreater(metrics["memory"]["sampled_tree_lifetime_cpu_s"], 0)
            self.assertEqual(metrics["memory"]["cpu_unreadable_samples"], 0)
            self.assertNotIn(os.getpid(),
                             metrics["memory"]["os_peak_ws_per_pid"])
            self.assertFalse((home / "python_worker_private.json").exists())
        finally:
            bench.cleanup_owned_workspace(home)
        self.assertFalse(home.exists())


if __name__ == "__main__":
    unittest.main()
