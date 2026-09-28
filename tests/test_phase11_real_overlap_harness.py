"""Contract tests for the real Phase 11 SSH overlap smoke harness."""
from __future__ import annotations

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from akuz_fetch import ConnectConfig
from scripts import bench_phase11_ssh_overlap as bench
from tests.test_fetch_performance import FakeSSH


class Phase11RealOverlapHarnessTests(unittest.TestCase):
    def config(self, root: Path):
        return ConnectConfig(
            "example.test", 22, "reader", "", "", "",
            "/srv/akuz", root / "downloads", "*.log", "", False)

    def spec(self, data: bytes):
        return bench.SourceSpec(
            "20260923", "20260923_server.log",
            "/srv/akuz/20260923_server.log",
            len(data), 1, 2)

    def test_fixed_fetch_writes_exact_prefix_and_checks_identity(self):
        with TemporaryDirectory(prefix="akuz_phase11_fixed_") as td:
            root = Path(td)
            data = b"12:00:00.000,AKUZ,s,user: stable\n"
            cfg = self.config(root)
            spec = self.spec(data)
            listed = dict(
                path=spec.path, name=spec.name, size=spec.bound,
                device=spec.device, inode=spec.inode)
            ssh = FakeSSH(data)
            before = ((1, 2, len(data), 100), "")
            destination = root / spec.name
            with patch.object(bench, "_connect", return_value=ssh), \
                 patch.object(bench, "_listing", return_value=[listed]), \
                 patch.object(bench, "_remote_metadata",
                              side_effect=[before, before]):
                snapshot = bench._fetch_fixed(cfg, spec, destination)
            self.assertTrue(ssh.closed)
            self.assertEqual(snapshot.item, spec)
            self.assertEqual(snapshot.path.read_bytes(), data)
            self.assertEqual(snapshot.bytes, len(data))
            self.assertEqual(snapshot.digest, hashlib.sha256(data).hexdigest())

    def test_fixed_fetch_rejects_rotation_and_removes_partial_destination(self):
        with TemporaryDirectory(prefix="akuz_phase11_rotate_") as td:
            root = Path(td)
            data = b"12:00:00.000,AKUZ,s,user: stable\n"
            cfg = self.config(root)
            spec = self.spec(data)
            listed = dict(
                path=spec.path, name=spec.name, size=spec.bound,
                device=spec.device, inode=spec.inode)
            before = ((1, 2, len(data), 100), "")
            after = ((1, 9, len(data), 100), "")
            destination = root / spec.name
            with patch.object(bench, "_connect", return_value=FakeSSH(data)), \
                 patch.object(bench, "_listing", return_value=[listed]), \
                 patch.object(bench, "_remote_metadata",
                              side_effect=[before, after]):
                with self.assertRaisesRegex(
                        AssertionError, "rotated/truncated"):
                    bench._fetch_fixed(cfg, spec, destination)
            self.assertFalse(destination.exists())

    @staticmethod
    def mode(name: str, digest="a" * 64, manifest="b" * 64):
        return dict(
            mode=name, wall_s=2.0 if name == "serial" else 1.4,
            process_cpu_s=1.0, fetched=2, parsed=2,
            outputs=[
                dict(day="20260923", events=10, physical_lines=20,
                     report_manifest_sha256=manifest),
                dict(day="20260924", events=11, physical_lines=21,
                     report_manifest_sha256=manifest),
            ],
            fetch={"20260923": .2, "20260924": .2},
            parse={
                "20260923": dict(wall_s=.5, process_cpu_s=.4, events=10,
                    physical_lines=20, report_bytes=1000,
                    report_manifest_sha256=manifest),
                "20260924": dict(wall_s=.5, process_cpu_s=.4, events=11,
                    physical_lines=21, report_bytes=1000,
                    report_manifest_sha256=manifest),
            },
            source_sha={"20260923": digest, "20260924": digest},
            sampled_peak_ws_bytes=100, sampled_peak_private_bytes=90,
            memory_samples=20, unreadable_memory_samples=0,
            workspace_bytes=2000)

    def test_run_persists_only_equality_not_source_or_report_hashes(self):
        with TemporaryDirectory(prefix="akuz_phase11_run_") as td:
            root = Path(td)
            diagnostics = root / "diagnostics"
            diagnostics.mkdir()
            cfg = self.config(root)
            specs = (
                bench.SourceSpec("20260923", "a.log", "/srv/a.log", 10, 1, 2),
                bench.SourceSpec("20260924", "b.log", "/srv/b.log", 20, 1, 3),
            )
            calls = []
            def fake_mode(config, found, mode_root, *, overlap):
                calls.append(overlap)
                return self.mode("overlap" if overlap else "serial")
            with patch.object(bench, "load_config", return_value=cfg), \
                 patch.object(bench, "_discover", return_value=specs), \
                 patch.object(bench, "_run_mode", side_effect=fake_mode):
                result = bench.run(Path("cfg"), root, diagnostics)
            self.assertEqual(calls, [False, True])
            self.assertTrue(result["source_sha_equal"])
            self.assertTrue(result["report_manifest_equal"])
            self.assertTrue(result["workspace_cleaned"])
            self.assertFalse(result["raw_payload_saved"])
            self.assertNotIn("source_sha", result["serial"])
            self.assertNotIn("report_manifest_sha256",
                             result["serial"]["outputs"][0])
            self.assertNotIn("report_manifest_sha256",
                             result["serial"]["parse"]["20260923"])

    def test_run_rejects_source_or_report_mismatch(self):
        with TemporaryDirectory(prefix="akuz_phase11_mismatch_") as td:
            root = Path(td)
            diagnostics = root / "diagnostics"
            diagnostics.mkdir()
            cfg = self.config(root)
            specs = (
                bench.SourceSpec("20260923", "a.log", "/srv/a.log", 10, 1, 2),
                bench.SourceSpec("20260924", "b.log", "/srv/b.log", 20, 1, 3),
            )
            serial = self.mode("serial")
            bad_source = self.mode("overlap", digest="c" * 64)
            with patch.object(bench, "load_config", return_value=cfg), \
                 patch.object(bench, "_discover", return_value=specs), \
                 patch.object(bench, "_run_mode",
                              side_effect=[serial, bad_source]):
                with self.assertRaisesRegex(AssertionError, "source SHA"):
                    bench.run(Path("cfg"), root, diagnostics)

            bad_report = self.mode("overlap", manifest="d" * 64)
            with patch.object(bench, "load_config", return_value=cfg), \
                 patch.object(bench, "_discover", return_value=specs), \
                 patch.object(bench, "_run_mode",
                              side_effect=[self.mode("serial"), bad_report]):
                with self.assertRaisesRegex(AssertionError, "report manifest"):
                    bench.run(Path("cfg"), root, diagnostics)


if __name__ == "__main__":
    unittest.main()
