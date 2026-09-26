"""Phase 8 numeric-only counters leave error fingerprints and reports intact."""
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
import random
import unittest

from akuz_analytics import recognize_error
from akuz_html_explorer import generate


class Phase8ErrorProbeTests(unittest.TestCase):
    def test_counter_categories_preserve_unchanged_results(self):
        cases = [
            "12:00:00.000,AKUZ,r,user: Normal message",
            "12:00:00.000,AKUZ,r,user: System.Exception: failed\n at A.B()",
            "12:00:00.000,AKUZ,r,user: serialization error",
            "12:00:00.000,AKUZ,r,user: timeout",
            "12:00:00.000,AKUZ,r,user: Normal message\nSystem.Exception: late",
            "12:00:00.000,AKUZ,r,user: Unicode \u0131 \u0130 \u0345",
            "12:00:00.000,AKUZ,r,user: Normal\n" + "A" * 70000,
        ]
        rng = random.Random(8)
        cases += [
            "12:00:00.000,AKUZ,r,user: " +
            "".join(rng.choice(("ERROR", "xml", "\u0131", "<tag>", "failed", " ",
                                "Exception", "timeout", "\n", "Акуз"))
                    for _ in range(rng.randint(10, 250)))
            for _ in range(200)
        ]
        counts = Counter()
        for raw in cases:
            self.assertEqual(recognize_error(raw),
                             recognize_error(raw, diagnostics=counts))
        self.assertEqual(counts["error_recognize_calls"], len(cases))
        self.assertEqual(
            counts["error_recognize_calls"],
            counts["error_exception_events"]+counts["error_serial_events"]+
            counts["error_firstline_events"]+counts["error_no_match_events"])
        self.assertGreater(counts["error_truncated_events"], 0)
        self.assertGreater(counts["error_ascii_probe_events"], 0)
        self.assertGreater(counts["error_probe_chars"], 0)

    def test_report_only_emits_numeric_diagnostics(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "synthetic.log"
            source.write_text(
                "12:00:00.000,AKUZ,r,user: Normal secretword\n"
                "12:00:01.000,AKUZ,r,user: System.SerializationException: fake\n"
                " at AKUZ.Service.Method()\n"
                "12:00:02.000,AKUZ,r,user: serialization error\n",
                encoding="utf-8",
            )
            result = generate(source, root / "report", None, 10, 5)
            self.assertEqual(result["events"], 3)
            trace = (root / "diagnostics" / "performance.txt").read_text("utf-8")
            done = next(line for line in trace.splitlines()
                        if "stage=generate.parse status=done" in line)
            fields = dict(item.split("=", 1) for item in done.split()
                          if "=" in item)
            self.assertEqual(int(fields["error_recognize_calls"]), 3)
            self.assertEqual(int(fields["error_no_match_events"]), 1)
            self.assertEqual(int(fields["error_exception_events"]), 1)
            self.assertEqual(int(fields["error_serial_events"]), 1)
            self.assertNotIn("secretword", trace)
            self.assertNotIn("Method()", trace)


if __name__ == "__main__":
    unittest.main()
