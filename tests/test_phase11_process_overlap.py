"""Contract tests for Phase 11 process-isolated pair experiment."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from akuz_fetch import ConnectConfig
from scripts import bench_phase11_process_overlap as bench
from scripts.bench_phase11_ssh_overlap import SourceSpec


def synthetic_row(mode: str, wall: float, second_fetch: float,
                  first_parse: float, digest="a" * 64,
                  manifest="b" * 64):
    row = dict(
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
        sampled_peak_ws_bytes=220,
        sampled_peak_private_bytes=190,
        memory_samples=20, unreadable_memory_samples=0,
        workspace_bytes=1000)
    if mode == "process":
        row.update(parent_cpu_s=2.0, child_cpu_s=1.0,
                   max_sampled_processes=2,
                   fetch_ready_latency={
                       "20260923": 1.0,
                       "20260924": second_fetch + 2.0})

    return row


class Sender:
    def __init__(self):
        self.values = []
        self.closed = False

    def send(self, value):
        self.values.append(value)

    def close(self):
        self.closed = True


class Phase11ProcessOverlapTests(unittest.TestCase):
    def config(self, root):
        return ConnectConfig(
            "example.test", 22, "reader", "", "", "",
            "/srv/akuz", root / "downloads", "*.log", "", False)

    def specs(self):
        return (
            SourceSpec("20260923", "a.log", "/srv/a.log", 10, 1, 2),
            SourceSpec("20260924", "b.log", "/srv/b.log", 20, 1, 3),
        )

    def test_summary_requires_two_trials_and_preserves_pair_metrics(self):
        rows = [
            synthetic_row("serial", 10.0, 4.0, 5.0),
            synthetic_row("process", 8.0, 6.0, 5.2),
            synthetic_row("process", 9.0, 7.0, 5.4),
            synthetic_row("serial", 12.0, 4.5, 5.1),
        ]
        serial = bench._summary(rows, "serial")
        process = bench._summary(rows, "process")
        self.assertEqual(serial["wall_median_s"], 11.0)
        self.assertEqual(process["wall_median_s"], 8.5)
        self.assertEqual(process["second_fetch_values_s"], [6.0, 7.0])
        self.assertEqual(process["second_ready_values_s"], [8.0, 9.0])
        self.assertEqual(process["first_parse_values_s"], [5.2, 5.4])
        with self.assertRaisesRegex(AssertionError, "two trials"):
            bench._summary(rows[:2], "serial")

    def test_sanitize_removes_content_hashes_but_keeps_numeric_evidence(self):
        row = synthetic_row("process", 8.0, 6.0, 5.2)
        clean = bench._sanitize(row)
        self.assertNotIn("source_sha", clean)
        self.assertNotIn("report_manifest_sha256", clean["outputs"][0])
        self.assertNotIn(
            "report_manifest_sha256", clean["parse"]["20260923"])
        self.assertEqual(clean["child_cpu_s"], 1.0)
        self.assertEqual(clean["max_sampled_processes"], 2)

    def test_child_error_ipc_exposes_only_exception_type(self):
        sender = Sender()
        cfg = SimpleNamespace()
        spec = self.specs()[1]
        with patch.object(
                bench, "_fetch_fixed",
                side_effect=RuntimeError("SECRET /srv/private/path")):
            with self.assertRaises(RuntimeError):
                bench._child_fetch(cfg, spec, "unused", sender)
        self.assertEqual(sender.values, [("error", "RuntimeError")])
        self.assertTrue(sender.closed)

    def test_run_balanced_order_and_strips_private_hashes(self):
        with TemporaryDirectory(prefix="akuz_phase11_process_test_") as td:
            root = Path(td)
            diagnostics = root / "diag"
            diagnostics.mkdir()
            cfg = self.config(root)
            serial = [
                synthetic_row("serial", 10.0, 4.0, 5.0),
                synthetic_row("serial", 11.0, 4.2, 5.1),
            ]
            process = [
                synthetic_row("process", 8.0, 6.0, 5.4),
                synthetic_row("process", 8.5, 6.2, 5.5),
            ]
            sc = pc = 0
            calls = []

            def fake_serial(config, specs, mode_root, *, overlap):
                nonlocal sc
                self.assertFalse(overlap)
                calls.append("serial")
                value = serial[sc]
                sc += 1
                return value

            def fake_process(config, specs, mode_root):
                nonlocal pc
                calls.append("process")
                value = process[pc]
                pc += 1
                return value

            with patch.object(bench, "load_config", return_value=cfg), \
                 patch.object(bench, "_discover", return_value=self.specs()), \
                 patch.object(bench, "_run_mode", side_effect=fake_serial), \
                 patch.object(
                     bench, "_process_pair_mode", side_effect=fake_process):
                result = bench.run(Path("cfg"), root, diagnostics)

            self.assertEqual(calls, list(bench.ORDER))
            self.assertEqual(result["order"], list(bench.ORDER))
            self.assertTrue(result["source_sha_equal"])
            self.assertTrue(result["report_manifest_equal"])
            self.assertTrue(result["workspace_cleaned"])
            self.assertFalse(result["raw_payload_saved"])
            self.assertFalse(result["app_cache_changed"])
            for trial in result["trials"]:
                self.assertNotIn("source_sha", trial)
                self.assertNotIn(
                    "report_manifest_sha256", trial["outputs"][0])

    def test_run_rejects_cross_mode_source_drift(self):
        with TemporaryDirectory(prefix="akuz_phase11_process_bad_") as td:
            root = Path(td)
            diagnostics = root / "diag"
            diagnostics.mkdir()
            cfg = self.config(root)
            serial = synthetic_row("serial", 10.0, 4.0, 5.0)
            process = synthetic_row(
                "process", 8.0, 6.0, 5.2, digest="c" * 64)
            with patch.object(bench, "load_config", return_value=cfg), \
                 patch.object(bench, "_discover", return_value=self.specs()), \
                 patch.object(bench, "_run_mode", return_value=serial), \
                 patch.object(bench, "_process_pair_mode", return_value=process):
                with self.assertRaisesRegex(AssertionError, "source SHA"):
                    bench.run(Path("cfg"), root, diagnostics)


if __name__ == "__main__":
    unittest.main()
