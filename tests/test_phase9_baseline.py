"""Phase 9.0: isolated Windows metrics and precise content normalization."""
from pathlib import Path
import json
import sys
from tempfile import TemporaryDirectory
import unittest

from scripts.bench_phase9_baseline import (canonical_hash, create_sources,
                                           file_manifest, run)


class Phase9BaselineTests(unittest.TestCase):
    def test_snapshot_hash_and_bad_utf8_are_synthetic_only(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            metadata = create_sources(root / "synthetic_sources", 3, 48)
            self.assertEqual([row["expected_events"] for row in metadata], [3, 3, 4])
            self.assertNotEqual(metadata[0]["sha256"], metadata[1]["sha256"])
            self.assertIn(b"\xff", (root / "synthetic_sources" /
                                   "20260926_C.log").read_bytes())

    def test_catalog_normalizes_only_ephemeral_merge_name(self):
        with TemporaryDirectory() as td:
            directory = Path(td)
            data = directory / "data"
            data.mkdir()
            file = data / "catalog.js"
            file.write_bytes(b'window.AKUZ_DATA={"meta":{"source":"akuz-v4-merge-ABCD.jsonl"},'
                             b'"rows":[[1,0]]};\n')
            first = file_manifest(directory)
            file.write_bytes(b'window.AKUZ_DATA={"meta":{"source":"akuz-v4-merge-EFGH.jsonl"},'
                             b'"rows":[[1,0]]};\n')
            self.assertEqual(first, file_manifest(directory))
            file.write_bytes(b'window.AKUZ_DATA={"meta":{"source":"akuz-v4-merge-EFGH.jsonl"},'
                             b'"rows":[[2,0]]};\n')
            self.assertNotEqual(first, file_manifest(directory))

    def test_provenance_preserves_identity_but_ignores_generated_clock(self):
        with TemporaryDirectory() as td:
            folder = Path(td)
            path = folder / "provenance.json"
            value = dict(generated="2026-01-01T00:00:00",
                sources=[dict(remote_path="C:/A/synthetic.log",
                              sha256="synthetic-sha")], events=3)
            path.write_text(json.dumps(value), encoding="utf-8")
            first = file_manifest(folder)
            value["generated"] = "2026-02-02T00:00:00"
            path.write_text(json.dumps(value), encoding="utf-8")
            self.assertEqual(first, file_manifest(folder))
            value["sources"][0]["remote_path"] = "C:/B/synthetic.log"
            path.write_text(json.dumps(value), encoding="utf-8")
            self.assertNotEqual(first, file_manifest(folder))

    @unittest.skipUnless(sys.platform == "win32", "Windows OS counters")
    def test_isolated_build_modes_and_memory(self):
        result = run(6, 96, 1, .02)
        fresh = result["results"][0]["fresh"]
        warm = result["results"][0]["warm"]
        self.assertEqual((fresh["events"], fresh["reports"]), (19, 3))
        self.assertFalse(fresh["reused"])
        self.assertTrue(warm["reused"])
        self.assertEqual(fresh["reports_sha256"], warm["reports_sha256"])
        self.assertEqual(fresh["analytics_sql_sha256"], warm["analytics_sql_sha256"])
        self.assertEqual(result["duplicate_source_sha256"],
                         result["source_snapshots"][0]["sha256"])
        combined = result["cache_scenarios"]["combined_only_miss"]
        single = result["cache_scenarios"]["single_only_miss"]
        mixed = result["cache_scenarios"]["mixed_cache"]
        self.assertEqual(combined["reports_sha256"], fresh["reports_sha256"])
        self.assertEqual(single["reports_sha256"], fresh["reports_sha256"])
        self.assertEqual((mixed["events"], mixed["reports"]), (25, 4))
        self.assertGreater(fresh["memory"]["samples"], 0)
        self.assertGreater(fresh["memory"]["peak_tree_working_set_bytes_sampled"], 0)
        self.assertTrue(fresh["memory"]["peak_working_set_bytes_per_pid_os"])
        self.assertFalse(result["raw_payload_saved"])

    def test_failed_report_generator_leaves_no_published_cache_entry(self):
        from akuz_app import _publish
        from akuz_store import load_store
        with TemporaryDirectory() as td:
            root = Path(td)
            source = root / "20260925_synthetic.log"
            source.write_text("12:00:00.000,AKUZ,r,u: synthetic\n",
                              encoding="utf-8")
            store = load_store(root)

            def fail_generator(raw, output, base, chunk, top):
                output.mkdir(parents=True)
                (output / "index.html").write_text("temporary", encoding="utf-8")
                raise OSError("synthetic writer failure")

            with self.assertRaisesRegex(OSError, "synthetic writer failure"):
                _publish(root, store, "synthetic-key", source, None, [],
                         "synthetic", "single", gen_fn=fail_generator)
            self.assertEqual(store["reports"], {})
            self.assertFalse(list((root / "reports").glob("*.building")))
            self.assertFalse(list((root / "reports").glob("v4_*")))
            self.assertFalse((root / "cache" / "inventory.json").exists())

    def test_error_branch_profiler_only_emits_metrics(self):
        from scripts.bench_phase9_errors import profile
        result = profile(3, 1)
        self.assertEqual(len(result["records"]), 16)
        self.assertFalse(result["raw_payload_saved"])
        for samples in result["records"].values():
            self.assertEqual(samples[0]["calls"], 3)
            self.assertGreaterEqual(samples[0]["probe_chars"], 1)


if __name__ == "__main__":
    unittest.main()
