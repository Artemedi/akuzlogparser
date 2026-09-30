"""Phase 14 P14-02 raw-shard-only orjson experiment guards."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import akuz_html_explorer
from akuz_html_explorer import generate
from scripts import bench_phase14_raw_orjson as benchmark


HAS_ORJSON = importlib.util.find_spec("orjson") is not None


@unittest.skipUnless(HAS_ORJSON, "orjson experiment dependency not installed")
class Phase14RawOrjsonEquivalenceTests(unittest.TestCase):
    def test_raw_string_array_matches_stdlib_bytes(self):
        sample = [
            "<tag>& русский    ",
            "\\quoted\"\nline",
            "\x00\x01\t\r",
            "replacement � and emoji 🙂",
        ]
        self.assertEqual(
            benchmark._raw_orjson_compact(sample),
            akuz_html_explorer._json_compact(sample),
        )

    def test_catalog_like_objects_always_use_stdlib(self):
        # Tiny floats are a known class where semantically equal JSON may use
        # different exponent spelling across encoders. P14-02 must not expose
        # catalog objects to orjson at all.
        sample = {
            "small": 1e-6,
            "rows": [[1, "component", 0.000001]],
            "flags": [True, False, None],
        }
        self.assertEqual(
            benchmark._raw_orjson_compact(sample),
            akuz_html_explorer._json_compact(sample),
        )

    def test_generated_report_files_are_byte_identical(self):
        with tempfile.TemporaryDirectory(
                prefix="akuz_p14_raw_orjson_test_") as tmp:
            root = Path(tmp)
            log = root / "sample.log"
            log.write_text(
                "12:00:00.000,AKUZ,req,user: first <tag>& value\n"
                "12:00:01.000,AKUZ,req,user: second   value\n"
                "12:00:02.000,AKUZ,req,user: third   value\n",
                encoding="utf-8",
            )
            baseline = root / "baseline"
            candidate = root / "candidate"
            generate(log, baseline, None, 10, 10)
            with patch.object(
                    akuz_html_explorer, "_json_compact",
                    benchmark._raw_orjson_compact):
                generate(log, candidate, None, 10, 10)

            baseline_files = {
                path.relative_to(baseline).as_posix(): path.read_bytes()
                for path in baseline.rglob("*") if path.is_file()
            }
            candidate_files = {
                path.relative_to(candidate).as_posix(): path.read_bytes()
                for path in candidate.rglob("*") if path.is_file()
            }
            self.assertEqual(candidate_files, baseline_files)


class Phase14RawOrjsonHarnessTests(unittest.TestCase):
    def test_combined_scratch_name_is_deterministic(self):
        with tempfile.TemporaryDirectory(
                prefix="akuz_p14_merge_name_") as tmp:
            root = Path(tmp)
            names = []
            for _ in range(2):
                with benchmark._deterministic_merge_tempfile(
                        mode="w",
                        suffix=".jsonl",
                        prefix="akuz-v4-merge-",
                        dir=root,
                        delete=False) as stream:
                    names.append(Path(stream.name).name)
                    stream.write("marker")
                Path(stream.name).unlink(missing_ok=True)
            self.assertEqual(
                names,
                ["akuz-v4-merge-phase14-fixed.jsonl"] * 2,
            )


class Phase14RawOrjsonSurfaceTests(unittest.TestCase):
    def test_safe_manifest_mismatch_reports_only_structure(self):
        reference = {
            "v4_20990101_000001_00000001": {
                "events": 10,
                "kind": "single",
                "producer_sha256": {
                    "data/catalog.js": "a" * 64,
                    "data/raw_00000.js": "b" * 64,
                },
            }
        }
        current = {
            "v4_20990101_000001_00000001": {
                "events": 10,
                "kind": "single",
                "producer_sha256": {
                    "data/catalog.js": "a" * 64,
                    "data/raw_00000.js": "c" * 64,
                },
            }
        }
        detail = benchmark._safe_parity_detail(
            "manifest", reference, current)
        self.assertEqual(
            detail,
            "manifest:v4_20990101_000001_00000001:data/raw_00000.js",
        )
        self.assertNotIn("b" * 64, detail)
        self.assertNotIn("c" * 64, detail)

    def test_public_summary_redacts_private_evidence(self):
        result = {
            "setup": {"source_bytes": 123, "reports": 4},
            "order": list(benchmark.ORDER),
            "orjson_version": "3.12.0",
            "trials": [],
            "baseline_median": {"wall_s": 10.0},
            "candidate_median": {"wall_s": 5.0},
            "wall_reduction_pct": 50.0,
            "cpu_reduction_pct": 40.0,
            "generate_reduction_pct": 45.0,
            "json_reduction_pct": 70.0,
            "exact_equivalence": True,
            "analytics_equivalence": True,
            "downloads_unchanged": True,
            "host": "secret-host",
            "remote_path": "/secret/path",
            "digest": "secret-digest",
        }
        rendered = repr(benchmark._public(result))
        self.assertNotIn("secret-host", rendered)
        self.assertNotIn("/secret/path", rendered)
        self.assertNotIn("secret-digest", rendered)


if __name__ == "__main__":
    unittest.main()
