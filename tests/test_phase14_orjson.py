"""Phase 14 P14-01 orjson experiment guards."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import akuz_html_explorer
from akuz_html_explorer import generate
from scripts import bench_phase14_orjson as benchmark


HAS_ORJSON = importlib.util.find_spec("orjson") is not None


@unittest.skipUnless(HAS_ORJSON, "orjson experiment dependency not installed")
class Phase14OrjsonEquivalenceTests(unittest.TestCase):
    def test_compact_encoder_matches_supported_report_types(self):
        sample = {
            "text": "<tag>& русский    ",
            "rows": [
                [1, 0, "12:00:00.000", 2, 3, None, "user", "message", 1, 1, 0, 0, 0],
                [2, 0, "", 0, 1, "req", "", "\\quoted\"\nline", 2, 3, 0, 1, 0],
            ],
            "tuple_like": [(12, "component", "kind", 99)],
            "flags": [True, False, None],
            "large": 1234567890123,
        }
        self.assertEqual(
            benchmark._orjson_compact(sample),
            akuz_html_explorer._json_compact(sample),
        )

    def test_generated_report_files_are_byte_identical(self):
        with tempfile.TemporaryDirectory(prefix="akuz_p14_orjson_test_") as tmp:
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
                    benchmark._orjson_compact):
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


class Phase14OrjsonSurfaceTests(unittest.TestCase):
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
            "json_reduction_pct": 80.0,
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
