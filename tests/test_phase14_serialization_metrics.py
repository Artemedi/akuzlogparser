"""Phase 14 serialization instrumentation; report bytes stay unchanged."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from akuz_html_explorer import generate, js_json


def legacy_js_json(value):
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            .replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("&", "\\u0026").replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029"))


class Phase14SerializationMetricsTests(unittest.TestCase):
    def test_refactored_js_json_is_byte_equivalent(self):
        sample = [
            "<script>&</script>",
            "line\u2028separator\u2029end",
            {"кириллица": "да", "n": 123, "nested": [True, None, ">"]},
        ]
        self.assertEqual(js_json(sample), legacy_js_json(sample))
        self.assertNotIn("<script>", js_json(sample))

    def test_generate_reports_serialization_breakdown_matches_files(self):
        with tempfile.TemporaryDirectory(prefix="akuz_phase14_") as tmp:
            root = Path(tmp)
            log = root / "sample.log"
            lines = []
            for n in range(12):
                lines.append(
                    f"12:{n:02}:00.000,AKUZ,r{n},user: "
                    f"normal event {n} <tag>& marker\n")
            log.write_text("".join(lines), encoding="utf-8")
            out = root / "reports" / "sample"
            meta = generate(log, out, None, 10, 10)

            trace = (root / "diagnostics" / "performance.txt").read_text(
                "utf-8")
            summary = next(
                line for line in trace.splitlines()
                if "stage=generate.serialization status=summary" in line)
            fields = dict(
                token.split("=", 1) for token in summary.split()
                if "=" in token)

            raw_bytes = sum(
                path.stat().st_size
                for path in (out / "data").glob("raw_*.js"))
            catalog_bytes = (out / "data" / "catalog.js").stat().st_size
            self.assertEqual(int(fields["shard_bytes"]), raw_bytes)
            self.assertEqual(int(fields["catalog_bytes"]), catalog_bytes)
            self.assertEqual(
                int(fields["report_files"]), len(meta["_output_hashes"]))
            self.assertEqual(
                int(fields["report_output_bytes"]),
                sum(size for _, size in meta["_output_hashes"].values()))

            shard_parts = sum(float(fields[key]) for key in (
                "shard_json_s", "shard_escape_s", "shard_io_s"))
            self.assertLessEqual(
                shard_parts, float(fields["shard_total_s"]) + 0.002)
            for key in (
                    "catalog_json_s", "catalog_escape_s", "catalog_io_s"):
                self.assertGreaterEqual(float(fields[key]), 0.0)
            self.assertNotIn("normal event 11", trace)


if __name__ == "__main__":
    unittest.main()
