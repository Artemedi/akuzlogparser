"""Contract tests for balanced replicated Phase 11 benchmark wrapper."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from akuz_fetch import ConnectConfig
from scripts import bench_phase11_ssh_overlap_replicated as bench
from scripts.bench_phase11_ssh_overlap import SourceSpec


def row(mode: str, wall: float, second_fetch: float,
        first_parse: float, digest="a" * 64, manifest="b" * 64):
    return dict(
        mode=mode, wall_s=wall, process_cpu_s=wall / 2,
        fetched=2, parsed=2,
        outputs=[
            dict(day="20260923", events=10, physical_lines=20,
                 report_manifest_sha256=manifest),
            dict(day="20260924", events=11, physical_lines=21,
                 report_manifest_sha256=manifest),
        ],
        fetch={"20260923": 1.0, "20260924": second_fetch},
        parse={
            "20260923": dict(
                wall_s=first_parse, process_cpu_s=1.0, events=10,
                physical_lines=20, report_bytes=100,
                report_manifest_sha256=manifest),
            "20260924": dict(
                wall_s=2.0, process_cpu_s=1.0, events=11,
                physical_lines=21, report_bytes=110,
                report_manifest_sha256=manifest),
        },
        source_sha={"20260923": digest, "20260924": digest},
        sampled_peak_ws_bytes=200,
        sampled_peak_private_bytes=180,
        memory_samples=20, unreadable_memory_samples=0,
        workspace_bytes=1000)


class Phase11ReplicatedTests(unittest.TestCase):
    def config(self, root):
        return ConnectConfig(
            "example.test", 22, "reader", "", "", "",
            "/srv/akuz", root / "downloads", "*.log", "", False)

    def specs(self):
        return (
            SourceSpec("20260923", "a.log", "/srv/a.log", 10, 1, 2),
            SourceSpec("20260924", "b.log", "/srv/b.log", 20, 1, 3),
        )

    def test_summary_requires_two_trials_and_preserves_raw_values(self):
        rows = [
            row("serial", 10.0, 4.0, 5.0),
            row("overlap", 8.0, 7.0, 5.5),
            row("overlap", 9.0, 8.0, 5.7),
            row("serial", 12.0, 4.5, 5.2),
        ]
        serial = bench._summary(rows, "serial")
        overlap = bench._summary(rows, "overlap")
        self.assertEqual(serial["wall_median_s"], 11.0)
        self.assertEqual(overlap["wall_median_s"], 8.5)
        self.assertEqual(serial["second_fetch_values_s"], [4.0, 4.5])
        self.assertEqual(overlap["first_parse_values_s"], [5.5, 5.7])
        with self.assertRaisesRegex(AssertionError, "two trials"):
            bench._summary(rows[:2], "serial")

    def test_run_uses_balanced_order_and_strips_content_hashes(self):
        with TemporaryDirectory(prefix="akuz_phase11_rep_") as td:
            root = Path(td)
            diagnostics = root / "diag"
            diagnostics.mkdir()
            cfg = self.config(root)
            prepared = {
                "serial": [row("serial", 10.0, 4.0, 5.0),
                           row("serial", 11.0, 4.2, 5.1)],
                "overlap": [row("overlap", 8.0, 7.0, 5.4),
                            row("overlap", 8.5, 7.2, 5.5)],
            }
            counts = {"serial": 0, "overlap": 0}
            calls = []
            def fake_mode(config, specs, mode_root, *, overlap):
                mode = "overlap" if overlap else "serial"
                calls.append(mode)
                value = prepared[mode][counts[mode]]
                counts[mode] += 1
                return value
            with patch.object(bench, "load_config", return_value=cfg), \
                 patch.object(bench, "_discover", return_value=self.specs()), \
                 patch.object(bench, "_run_mode", side_effect=fake_mode):
                result = bench.run(Path("cfg"), root, diagnostics)
            self.assertEqual(calls, list(bench.ORDER))
            self.assertTrue(result["source_sha_equal"])
            self.assertTrue(result["report_manifest_equal"])
            self.assertTrue(result["workspace_cleaned"])
            self.assertFalse(result["raw_payload_saved"])
            for trial in result["trials"]:
                self.assertNotIn("source_sha", trial)
                self.assertNotIn("report_manifest_sha256",
                                 trial["outputs"][0])
                self.assertNotIn("report_manifest_sha256",
                                 trial["parse"]["20260923"])

    def test_run_rejects_cross_trial_source_or_report_drift(self):
        with TemporaryDirectory(prefix="akuz_phase11_rep_bad_") as td:
            root = Path(td)
            diagnostics = root / "diag"
            diagnostics.mkdir()
            cfg = self.config(root)
            source_drift = [
                row("serial", 10, 4, 5),
                row("overlap", 8, 7, 5, digest="c" * 64),
            ]
            with patch.object(bench, "load_config", return_value=cfg), \
                 patch.object(bench, "_discover", return_value=self.specs()), \
                 patch.object(bench, "_run_mode", side_effect=source_drift):
                with self.assertRaisesRegex(AssertionError, "source SHA"):
                    bench.run(Path("cfg"), root, diagnostics)

            report_drift = [
                row("serial", 10, 4, 5),
                row("overlap", 8, 7, 5, manifest="d" * 64),
            ]
            with patch.object(bench, "load_config", return_value=cfg), \
                 patch.object(bench, "_discover", return_value=self.specs()), \
                 patch.object(bench, "_run_mode", side_effect=report_drift):
                with self.assertRaisesRegex(AssertionError, "report manifest"):
                    bench.run(Path("cfg"), root, diagnostics)


if __name__ == "__main__":
    unittest.main()
