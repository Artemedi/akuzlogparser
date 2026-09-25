"""Protect byte-for-byte event semantics while optimizing multiline assembly."""
from collections import Counter
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from akuz_log_parser import event_stream
from akuz_html_explorer import generate
from akuz_analytics import read_js


class EventStreamRegressionTests(unittest.TestCase):
    def parse(self, payload):
        with TemporaryDirectory() as temp:
            path=Path(temp)/"20260925_synthetic.log"
            path.write_bytes(payload)
            stats=Counter()
            events=list(event_stream(path,stats))
            return events,stats

    def test_preamble_continuations_windows_and_rollover(self):
        payload=(b"intro\r\nsecond\n"
                 b"23:59:59.999,AKUZ,s1,alice: first\r\n"
                 b"continuation\n\r\n"
                 b"user 'bob' 00:00:00.001: next\r\n"
                 b"not-a-header\n")
        events,stats=self.parse(payload)
        self.assertEqual(len(events),3)
        self.assertEqual([e["event_id"] for e in events],[1,2,3])
        self.assertEqual([e["day_offset"] for e in events],[0,0,1])

        self.assertEqual([(e["start_line"],e["end_line"]) for e in events],
                         [(1,2),(3,5),(6,7)])
        self.assertEqual([e["message"] for e in events],
                         ["intro\nsecond","first\ncontinuation\n",
                          "next\nnot-a-header"])
        self.assertEqual([e["raw"] for e in events],
                         ["intro\nsecond",
                          "23:59:59.999,AKUZ,s1,alice: first\ncontinuation\n",
                          "user 'bob' 00:00:00.001: next\nnot-a-header"])
        self.assertEqual(stats["physical_lines"],7)
        self.assertEqual(stats["continuation_lines"],5)
        self.assertEqual(stats["preamble_events"],1)
        self.assertEqual(stats["midnight_rollovers"],1)
        self.assertEqual(stats["windows_trace_events"],1)

    def test_invalid_utf8_and_missing_separator(self):
        events,stats=self.parse(b"12:00:00.001,AKUZ,s1,unusual\xff\nmore\n")
        self.assertEqual(events[0]["message"],"unusual\ufffd\nmore")
        self.assertEqual(events[0]["user"],"")
        self.assertEqual(stats["replacement_chars"],1)
        self.assertEqual(stats["missing_user_separator"],1)

    def test_long_multiline_preserves_report_schema(self):
        lines=[("fragment %05d: "%i)+("X"*128) for i in range(1200)]
        first="System.Runtime.Serialization.SerializationException: synthetic"
        raw="12:01:00.000,AKUZ,req,user: "+first+"\n"+"\n".join(lines)
        with TemporaryDirectory() as temp:
            root=Path(temp)
            source=root/"20260925_synthetic.log"
            source.write_text(raw+"\n",encoding="utf-8")
            events,stats=self.parse((raw+"\n").encode())
            self.assertEqual(len(events),1)
            self.assertEqual(events[0]["message"],first+"\n"+"\n".join(lines))
            self.assertEqual(events[0]["raw"],raw)
            self.assertEqual(events[0]["end_line"],1201)
            self.assertEqual(stats["physical_lines"],1201)
            report=root/"report"
            meta=generate(source,report,date(2026,9,25),1000,35)
            catalog=read_js(report/"data"/"catalog.js","window.AKUZ_DATA=")
            shard=read_js(report/"data"/"raw_00000.js","window.AKUZ_RAW=")
            self.assertEqual(meta["events"],1)
            self.assertEqual(shard,[raw])
            self.assertEqual(catalog["rows"][0][8:11],[1,1201,0])
            self.assertEqual(len(catalog["errorFingerprints"]),1)
            trace=(root/"diagnostics"/"performance.txt").read_text(encoding="utf-8")
            self.assertIn("raw_chars="+str(len(raw)),trace)
            self.assertIn("max_event_chars="+str(len(raw)),trace)


if __name__=="__main__":
    unittest.main()
