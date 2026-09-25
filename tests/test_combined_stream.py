"""Streaming combined generation must match the historical JSONL bridge."""
from collections import Counter
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from akuz_app import _combine_sources, _iter_combined_sources
from akuz_html_explorer import generate
from akuz_analytics import read_js


class CombinedStreamTests(unittest.TestCase):
    def test_stream_and_jsonl_generate_identical_reports(self):
        with TemporaryDirectory() as temp:
            root=Path(temp)
            (root/"cache").mkdir()
            first=root/"20260924_first.log"
            second=root/"20260925_second.log"
            first.write_text(
                "23:59:59.999,AKUZ,req1,user: first\n"
                "extended "+"X"*12000+"\n"
                "00:00:00.001,AKUZ,req1,user: System.MyException: failed\n"
                " at AKUZ.Handle()\n",encoding="utf-8")
            second.write_text(
                "12:00:00.000,AKUZ,req2,user: за 20 ms\n"
                "continuation\n"
                "13:00:00.000,AKUZ,req2,user: another event\n",
                encoding="utf-8")
            selected=[
                dict(local=first,date="2026-09-24",remote=dict(name=first.name,mtime=1)),
                dict(local=second,date="2026-09-25",remote=dict(name=second.name,mtime=2))
            ]
            scratch=root/"cache"/"synthetic_joined.jsonl"
            count,lines=_combine_sources(selected,scratch,date(2026,9,24))
            self.assertEqual((count,lines),(4,7))
            regular=generate(scratch,root/"regular",date(2026,9,24),10,35)
            # A real app execution does not need the 2x-size JSONL bridge.
            scratch.write_bytes(b"")
            direct=generate(scratch,root/"direct",date(2026,9,24),10,35,
                event_source=_iter_combined_sources(selected,date(2026,9,24)),
                input_bytes=first.stat().st_size+second.stat().st_size)

            self.assertEqual(regular,direct)
            for name in ("data/catalog.js","data/raw_00000.js",
                         "index.html","index.js","event.js"):
                with self.subTest(name=name):
                    self.assertEqual((root/"regular"/name).read_bytes(),
                                     (root/"direct"/name).read_bytes())
            catalog=read_js(root/"direct"/"data"/"catalog.js","window.AKUZ_DATA=")
            self.assertEqual(catalog["meta"]["base_date"],"2026-09-24")
            self.assertEqual([row[1] for row in catalog["rows"]],[0,1,1,1])
            self.assertEqual([row[8:10] for row in catalog["rows"]],
                             [[1,2],[3,4],[1,2],[3,3]])
            self.assertEqual(len(catalog["errorFingerprints"]),1)

    def test_stream_errors_on_empty_selection(self):
        with self.assertRaisesRegex(Exception,"не обнаружены события"):
            list(_iter_combined_sources([],date(2026,9,24)))


if __name__=="__main__":
    unittest.main()
