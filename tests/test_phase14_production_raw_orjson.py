"""Production gates for Phase 14 raw-shard orjson integration."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import akuz_html_explorer as explorer


HAS_ORJSON = importlib.util.find_spec("orjson") is not None


class Phase14ProductionFlagTests(unittest.TestCase):
    def test_default_on_and_rollback_values(self):
        self.assertTrue(explorer._phase14_raw_orjson_requested({}))
        for value in ("1", "true", "YES", "on"):
            self.assertTrue(explorer._phase14_raw_orjson_requested(
                {"AKUZ_PHASE14_RAW_ORJSON": value}))
        for value in ("0", "false", "NO", "off"):
            self.assertFalse(explorer._phase14_raw_orjson_requested(
                {"AKUZ_PHASE14_RAW_ORJSON": value}))

    def test_invalid_explicit_value_fails(self):
        with self.assertRaisesRegex(
                ValueError, "AKUZ_PHASE14_RAW_ORJSON"):
            explorer._phase14_raw_orjson_requested(
                {"AKUZ_PHASE14_RAW_ORJSON": ""})

    def test_missing_optional_dependency_uses_stdlib(self):
        sample = ["<tag>&", "\\quoted\"\nline", "русский    "]
        with patch.object(explorer, "_orjson", None):
            self.assertFalse(explorer._phase14_raw_orjson_active({}))
            self.assertEqual(
                explorer._raw_json_compact(sample, use_orjson=True),
                explorer._json_compact(sample),
            )


@unittest.skipUnless(HAS_ORJSON, "orjson optional acceleration unavailable")
class Phase14ProductionByteParityTests(unittest.TestCase):
    def test_raw_array_bytes_match_stdlib(self):
        sample = [
            "<tag>& русский    ",
            "\\quoted\"\nline",
            "\x00\x01\t\r",
            "replacement � and emoji 🙂",
            "x" * 100_000,
        ]
        self.assertEqual(
            explorer._raw_json_compact(sample, use_orjson=True),
            explorer._json_compact(sample),
        )

    def test_generated_report_is_byte_identical(self):
        with tempfile.TemporaryDirectory(
                prefix="akuz_p14_prod_parity_") as tmp:
            root = Path(tmp)
            source = root / "20260923_server.log"
            rows = []
            for index in range(23):
                rows.append(
                    f"12:00:{index:02d}.000,AKUZ,req,user: "
                    f"event {index} <tag>& русский    \\quoted\"\n"
                )
            source.write_text("".join(rows), encoding="utf-8")
            baseline = root / "baseline"
            candidate = root / "candidate"

            with patch.dict(
                    os.environ, {"AKUZ_PHASE14_RAW_ORJSON": "0"},
                    clear=False):
                explorer.generate(source, baseline, None, 10, 10)
            with patch.dict(
                    os.environ, {"AKUZ_PHASE14_RAW_ORJSON": "1"},
                    clear=False):
                explorer.generate(source, candidate, None, 10, 10)

            baseline_files = {
                path.relative_to(baseline).as_posix(): path.read_bytes()
                for path in baseline.rglob("*") if path.is_file()
            }
            candidate_files = {
                path.relative_to(candidate).as_posix(): path.read_bytes()
                for path in candidate.rglob("*") if path.is_file()
            }
            self.assertEqual(candidate_files, baseline_files)


if __name__ == "__main__":
    unittest.main()
