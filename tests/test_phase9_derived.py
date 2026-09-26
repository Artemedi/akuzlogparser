"""Phase 9.1: verify derived values do not consume report-specific coordinates."""
from collections import Counter
from dataclasses import replace
import unittest

from akuz_analytics import recognize_error
from akuz_derived import Derivers, derive_event
from akuz_log_parser import classify, normalize, extract_duration


def normal_functions():
    return Derivers(classify, normalize, extract_duration, recognize_error,
                    True, True, True, True)


def identity(event):
    value = derive_event(event, Counter(), normal_functions())
    return (value.category, value.normalized_pattern, value.duration,
            value.error_fp, value.replacement_chars, value.headline)


class DerivedEventTests(unittest.TestCase):
    def test_context_does_not_change_derived_values(self):
        originals = [
            ("12:00:00.000,AKUZ,req,user: normal", "normal"),
            ("12:00:00.000,AKUZ,req,user: за 5 ms", "за 5 ms"),
            ("12:00:00.000,AKUZ,req,user: System.Exception: failed\n at Test()",
             "System.Exception: failed\n at Test()"),
            ("12:00:00.000,AKUZ,req,user: invalid \ufffd",
             "invalid \ufffd"),
        ]
        for raw, message in originals:
            ev = dict(raw=raw, message=message, category=None,
                      event_id=1, day_offset=0, source_file="A", start_line=1)
            expected = identity(ev)
            different = dict(ev, event_id=818, day_offset=9,
                             source_file="B", start_line=50000)
            with self.subTest(message=message[:18]):
                self.assertEqual(identity(different), expected)
                self.assertEqual(expected[4], raw.count("\ufffd"))
                self.assertEqual(expected[5], message.split("\n")[0].strip()[:1400])
                self.assertEqual(expected[0], classify(message))
                self.assertEqual(expected[1], normalize(message))
                self.assertEqual(expected[2], extract_duration(message))
                match = recognize_error(raw)
                self.assertEqual(expected[3], match["fp"] if match else None)

    def test_legacy_injections_and_preclassified_input(self):
        calls = []
        def classify_old(text):
            calls.append("classify")
            return "legacy-label"
        def duration_old(text):
            calls.append("duration")
            return 7.0, "legacy"
        def error_old(raw):
            calls.append("error")
            return {"fp":"abc"}
        fns = Derivers(classify_old,lambda text:text[::-1],
                       duration_old,error_old,False,False,False,False)
        ev = dict(raw="X\ufffd",message="a\nb",category="")
        found = derive_event(ev,Counter(),fns)
        self.assertEqual(calls,["classify","duration","error"])
        self.assertEqual((found.category,found.normalized_pattern,
            found.duration,found.error_fp,found.replacement_chars,found.headline),
            ("legacy-label","b\na",(7.0,"legacy"),"abc",1,"a"))
        calls.clear()
        found = derive_event(dict(ev,category="already"),Counter(),fns)
        self.assertEqual(found.category,"already")
        self.assertEqual(calls,["duration","error"])

    def test_diagnostics_and_category_are_unchanged(self):
        stats = Counter()
        ev = dict(raw="12:00:00.000,AKUZ,req,user: System.InvalidOperationException: failed",
                  message="System.InvalidOperationException: failed",category=None)
        item = derive_event(ev,stats,normal_functions())
        self.assertEqual(item.category,classify(ev["message"]))
        self.assertEqual(stats["classify_calls"],1)
        self.assertEqual(stats["error_recognize_calls"],1)
        self.assertEqual(stats["error_exception_events"],1)
        self.assertTrue(item.error_fp)
        self.assertGreaterEqual(item.folded_s,0)


if __name__ == "__main__":
    unittest.main()
