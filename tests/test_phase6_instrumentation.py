"""Phase 6 diagnostic counters; production report formats stay unchanged."""
from collections import Counter
from pathlib import Path
import tempfile
import unittest

from akuz_html_explorer import generate
from akuz_log_parser import classify


class Phase6InstrumentationTests(unittest.TestCase):
    def test_classifier_counters_and_default_api(self):
        stats = Counter()
        self.assertEqual(classify("notfound", diagnostics=stats), classify("notfound"))
        self.assertEqual(classify("failure\u0345", diagnostics=stats), classify("failure\u0345"))
        self.assertEqual(stats["classify_calls"], 2)
        self.assertEqual(stats["classify_regex_fallback_events"], 1)

    def test_generate_phase_sum_and_no_raw_payload_in_trace(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            log = root / "sample.log"
            log.write_text(
                "12:00:00.000,AKUZ,r1,user: notfound\n"
                "12:00:01.000,AKUZ,r2,user: failure\u0345\n",
                encoding="utf-8")
            meta = generate(log, root / "out", None, 10, 5)
            self.assertEqual(meta["events"], 2)
            trace = (root / "diagnostics" / "performance.txt").read_text("utf-8")
            done = next(line for line in trace.splitlines()
                        if "stage=generate.parse status=done" in line)
            fields = dict(item.split("=", 1) for item in done.split()
                          if "=" in item)
            for key in ("source_next_s", "classify_s", "normalize_s",
                        "duration_s", "errors_s", "shard_write_s", "other_s",
                        "thread_cpu_s", "classify_calls",
                        "classify_regex_fallback_events",
                        "classify_literal_path_events"):
                self.assertIn(key, fields)
            self.assertEqual(int(fields["classify_calls"]), 2)
            self.assertEqual(int(fields["classify_regex_fallback_events"]), 1)
            self.assertEqual(int(fields["classify_literal_path_events"]), 1)
            components = ("source_next_s", "classify_s", "normalize_s",
                          "duration_s", "errors_s", "shard_write_s", "other_s")
            self.assertAlmostEqual(sum(float(fields[k]) for k in components),
                                   float(fields["elapsed_s"]), delta=0.005)
            self.assertNotIn("notfound", trace)


if __name__ == "__main__":
    unittest.main()
