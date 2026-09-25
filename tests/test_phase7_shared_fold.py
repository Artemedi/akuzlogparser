"""Shared casefold preserves standalone helpers and streaming report semantics."""
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
import random
import unittest

from akuz_html_explorer import read_input, generate
from akuz_log_parser import classify, extract_duration


class Phase7FoldTests(unittest.TestCase):
    def test_original_api_matches_shared_fold(self):
        rng = random.Random(4760)
        tokens = ["notfound", "not found", "timeout", "TİMEOUT",
                  "истекло время ожидания", "общее время: 00:00:01.500",
                  "за 3.25 ms", "ß", "\u0345", "empty", "ERROR", "тайм-аут"]
        for _ in range(2500):
            text = "".join(rng.choice(tokens) + rng.choice(("", " ", "\n", "X"))
                           for _ in range(rng.randint(1, 8)))
            if rng.random() < 0.05:
                text = "X" * 64000 + text
            folded = text.casefold()
            self.assertEqual(classify(text), classify(text, folded=folded))
            self.assertEqual(extract_duration(text), extract_duration(text, folded=folded))

    def test_deferred_classification_keeps_input_stream_contract(self):
        with TemporaryDirectory() as td:
            log = Path(td) / "example.log"
            log.write_text("12:00:00.000,AKUZ,r1,user: not found\n"
                           " multiline continuation\n"
                           "12:00:01.000,AKUZ,r2,user: за 7 ms\n",
                           encoding="utf-8")
            normal = list(read_input(log, None, Counter()))
            delayed = list(read_input(log, None, Counter(), defer_classify=True))
            self.assertEqual(len(normal), len(delayed))
            for expected, incoming in zip(normal, delayed):
                self.assertEqual(incoming.get("category"), None)
                self.assertEqual(classify(incoming["message"]), expected["category"])
                self.assertEqual({k: v for k, v in expected.items() if k != "category"},
                                 incoming)

    def test_report_exposes_fold_time_and_keeps_classification_count(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            log = root / "example.log"
            log.write_text("12:00:00.000,AKUZ,r1,user: timeout\n"
                           "12:00:01.000,AKUZ,r2,user: за 3.25 ms\n",
                           encoding="utf-8")
            meta = generate(log, root / "out", None, 10, 5)
            self.assertEqual(meta["events"], 2)
            diagnostic = (root / "diagnostics" / "performance.txt").read_text("utf-8")
            done = next(line for line in diagnostic.splitlines()
                        if "stage=generate.parse status=done" in line)
            fields = dict(item.split("=", 1) for item in done.split() if "=" in item)
            self.assertIn("fold_s", fields)
            self.assertEqual(fields["classify_calls"], "2")
            parts = ("source_next_s", "classify_s", "normalize_s", "duration_s",
                     "errors_s", "shard_write_s", "fold_s", "other_s")
            self.assertAlmostEqual(sum(float(fields[k]) for k in parts),
                                   float(fields["elapsed_s"]), delta=0.008)


if __name__ == "__main__":
    unittest.main()
