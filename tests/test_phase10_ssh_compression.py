"""Tests for the bounded Phase 10 SSH compression smoke harness."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import bench_phase10_ssh_compression_smoke as bench


class Phase10CompressionSmokeTests(unittest.TestCase):
    def test_trace_summary_requires_one_transfer_and_parses_numeric_metrics(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            diag = root / "diagnostics"
            diag.mkdir()
            (diag / "performance.txt").write_text(
                "2026-09-28T00:00:00 stage=source.ssh.transfer status=summary "
                "bytes_received=123 bytes_expected=123 elapsed_s=1.25 mib_per_s=0.094\n",
                encoding="utf-8")
            self.assertEqual(bench._trace_summary(root), dict(
                bytes_received=123, bytes_expected=123,
                transfer_elapsed_s=1.25, payload_mib_s=0.094))

    def test_run_requires_same_digest_and_strips_digest_from_public_result(self):
        listing = [dict(name="20260923_server.log", size=123,
                        device=7, inode=8)]
        rows = [
            dict(mode="control", wall_s=2.0, cpu_s=1.0, bytes=123,
                 digest="a"*64, transfer=dict(
                     bytes_received=123, bytes_expected=123,
                     transfer_elapsed_s=1.5, payload_mib_s=0.08)),
            dict(mode="compressed", wall_s=1.0, cpu_s=1.2, bytes=123,
                 digest="a"*64, transfer=dict(
                     bytes_received=123, bytes_expected=123,
                     transfer_elapsed_s=.7, payload_mib_s=0.17)),
        ]
        with patch.object(bench, "load_config", return_value=SimpleNamespace()), \
             patch.object(bench, "list_remote", return_value=listing), \
             patch.object(bench, "_trial", side_effect=rows) as trial:
            result = bench.run(Path("cfg"), Path("root"), "20260923")
        self.assertEqual([c.args[2] for c in trial.call_args_list], [False, True])
        self.assertTrue(result["same_snapshot_sha"])
        self.assertNotIn("digest", result["control"])
        self.assertNotIn("digest", result["compressed"])
        self.assertFalse(result["wire_bytes_measured"])
        self.assertFalse(result["server_sshd_cpu_measured"])
        self.assertFalse(result["raw_payload_saved"])

    def test_run_rejects_digest_or_size_change(self):
        listing = [dict(name="20260923_server.log", size=123,
                        device=7, inode=8)]
        base = dict(mode="control", wall_s=1, cpu_s=1, bytes=123,
                    digest="a"*64, transfer={})
        for second, message in (
            (dict(base, mode="compressed", digest="b"*64),
             "identical source bytes"),
            (dict(base, mode="compressed", bytes=124),
             "Source size changed"),
        ):
            with self.subTest(message=message), \
                 patch.object(bench, "load_config",
                              return_value=SimpleNamespace()), \
                 patch.object(bench, "list_remote", return_value=listing), \
                 patch.object(bench, "_trial", side_effect=[base, second]):
                with self.assertRaisesRegex(AssertionError, message):
                    bench.run(Path("cfg"), Path("root"), "20260923")

    def test_run_rejects_missing_server_identity(self):
        listing = [dict(name="20260923_server.log", size=123,
                        device=None, inode=None)]
        with patch.object(bench, "load_config", return_value=SimpleNamespace()), \
             patch.object(bench, "list_remote", return_value=listing):
            with self.assertRaisesRegex(AssertionError, "identity unavailable"):
                bench.run(Path("cfg"), Path("root"), "20260923")


if __name__ == "__main__":
    unittest.main()
